from datetime import UTC, datetime, timedelta
from uuid import UUID

from baked.superiorsix.domain.exceptions import PredictionError, RoundNotFoundError
from baked.superiorsix.domain.models import Match, Prediction, PredictionChoice
from baked.superiorsix.repositories.predictions import PredictionRepository
from baked.superiorsix.services.matches import MatchService
from baked.superiorsix.services.rounds import RoundService

_CUTOFF = timedelta(minutes=10)
_USER_CHOICES = {PredictionChoice.HOME, PredictionChoice.AWAY, PredictionChoice.DRAW}


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

    async def submit(
        self,
        *,
        auth_id: UUID,
        match_id: int,
        prediction: str,
        now: datetime | None = None,
    ) -> Prediction:
        choice = _parse_user_choice(prediction)
        round_ = await self._require_current_round()
        match = await self._matches.get(match_id)

        if not match.use_match:
            raise PredictionError("Match is not selected for Super Six.")

        slate_kickoff = await self._earliest_kickoff_for_match(match)
        cutoff_at = (now or datetime.now(UTC)) + _CUTOFF
        if cutoff_at >= slate_kickoff:
            raise PredictionError("Past cutoff for prediction set.")

        assert round_.id is not None
        existing = await self._predictions.get_active(
            round_id=round_.id,
            auth_id=auth_id,
            match_id=match_id,
        )
        if existing is not None:
            existing.prediction = choice.value
            return await self._predictions.save(existing)

        return await self._predictions.add(
            Prediction(
                round_id=round_.id,
                auth_id=auth_id,
                match_id=match_id,
                prediction=choice.value,
            )
        )

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
    try:
        choice = PredictionChoice(value)
    except ValueError as exc:
        raise PredictionError("prediction must be one of: home, away, draw.") from exc
    if choice not in _USER_CHOICES:
        raise PredictionError("prediction must be one of: home, away, draw.")
    return choice
