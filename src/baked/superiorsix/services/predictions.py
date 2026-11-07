from datetime import UTC, datetime, timedelta
from uuid import UUID

from baked.superiorsix.domain.exceptions import PredictionError, RoundNotFoundError
from baked.superiorsix.domain.models import Match, Prediction, PredictionChoice
from baked.superiorsix.repositories.predictions import PredictionRepository
from baked.superiorsix.services.matches import MatchService
from baked.superiorsix.services.rounds import RoundService

_CUTOFF = timedelta(minutes=10)
_USER_CHOICES = {PredictionChoice.HOME, PredictionChoice.AWAY, PredictionChoice.DRAW}
_CHOICE_ALIASES = {
    "h": PredictionChoice.HOME,
    "home": PredictionChoice.HOME,
    "a": PredictionChoice.AWAY,
    "away": PredictionChoice.AWAY,
    "d": PredictionChoice.DRAW,
    "draw": PredictionChoice.DRAW,
}


class PredictionService:
    def __init__(
        self,
        predictions: PredictionRepository,
        rounds: RoundService,
        matches: MatchService,
    ) -> None:
        self._predictions = predictions
        self._rounds = rounds
        self._matches = matches

    async def get_for_user(
        self,
        *,
        auth_id: UUID,
        match_id: int,
    ) -> Prediction | None:
        round_ = await self._rounds.current_or_none()
        if round_ is None or round_.id is None:
            return None
        return await self._predictions.get_active(
            round_id=round_.id,
            auth_id=auth_id,
            match_id=match_id,
        )

    async def list_active_for_matches(
        self,
        *,
        round_id: int,
        match_ids: list[int],
    ) -> list[Prediction]:
        return await self._predictions.list_active_for_matches(
            round_id=round_id,
            match_ids=match_ids,
        )

    async def submit(
        self,
        *,
        auth_id: UUID,
        match_id: int,
        prediction: str,
        now: datetime | None = None,
        enforce_cutoff: bool = True,
    ) -> Prediction:
        choice = _parse_user_choice(prediction)
        round_ = await self._require_current_round()
        match = await self._matches.get(match_id)

        if not match.use_match:
            raise PredictionError("Match is not selected for Super Six.")

        if enforce_cutoff:
            slate_kickoff = await self._earliest_kickoff_for_match(match)
            cutoff_at = (now or datetime.now(UTC)) + _CUTOFF
            if cutoff_at >= slate_kickoff:
                raise PredictionError("Past cutoff for prediction set.")

        assert round_.id is not None
        existing = await self._predictions.get_row(
            round_id=round_.id,
            auth_id=auth_id,
            match_id=match_id,
        )
        if existing is not None:
            existing.prediction = choice.value
            existing.dropped = False
            return await self._predictions.save(existing)

        return await self._predictions.add(
            Prediction(
                round_id=round_.id,
                auth_id=auth_id,
                match_id=match_id,
                prediction=choice.value,
            )
        )

    async def submit_for_admin(
        self,
        *,
        auth_id: UUID,
        match_id: int,
        prediction: str,
        now: datetime | None = None,
    ) -> Prediction:
        """Admin submit on behalf of a player.

        Legacy admin ``addprediction`` never enforced the player cutoff; keep that
        convenience so ops can correct picks after lock.
        """
        return await self.submit(
            auth_id=auth_id,
            match_id=match_id,
            prediction=prediction,
            now=now,
            enforce_cutoff=False,
        )

    async def drop(self, prediction_id: int) -> Prediction:
        prediction = await self._predictions.get(prediction_id)
        if prediction is None:
            raise PredictionError(f"Prediction {prediction_id} not found.")
        if prediction.dropped:
            return prediction
        prediction.dropped = True
        return await self._predictions.save(prediction)

    async def _earliest_kickoff_for_match(self, match: Match) -> datetime:
        """Earliest kickoff among selected fixtures in the match's subround.

        Falls back to the match's own kickoff when ``subround_id`` is unset
        (pre-migration rows).
        """
        if match.subround_id is None:
            return _as_utc(match.match_date)
        selected = await self._matches.selected_matches_for_subround(match.subround_id)
        if not selected:
            return _as_utc(match.match_date)
        return min(_as_utc(fixture.match_date) for fixture in selected)

    async def _require_current_round(self):
        try:
            return await self._rounds.current()
        except RoundNotFoundError as exc:
            raise PredictionError(str(exc)) from exc


def earliest_kickoff_for_subround(match: Match, selected: list[Match]) -> datetime:
    """Earliest kickoff among ``selected`` fixtures that share ``match.subround_id``.

    Pure helper for unit tests. Prefer ``PredictionService`` submit path in app code.
    """
    if match.subround_id is None:
        return _as_utc(match.match_date)
    slate = [
        fixture
        for fixture in selected
        if fixture.use_match
        and fixture.game_number is not None
        and fixture.subround_id == match.subround_id
    ]
    if not slate:
        return _as_utc(match.match_date)
    return min(_as_utc(fixture.match_date) for fixture in slate)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _parse_user_choice(value: str) -> PredictionChoice:
    normalized = value.strip().lower()
    choice = _CHOICE_ALIASES.get(normalized)
    if choice is None or choice not in _USER_CHOICES:
        raise PredictionError("prediction must be one of: home, away, draw (or H/D/A).")
    return choice
