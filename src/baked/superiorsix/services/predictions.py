from datetime import UTC, date, datetime, timedelta
from uuid import UUID

from baked.superiorsix.domain.exceptions import PredictionError, RoundNotFoundError
from baked.superiorsix.domain.models import Match, Prediction, PredictionChoice
from baked.superiorsix.repositories.predictions import PredictionRepository
from baked.superiorsix.services.matches import MatchService
from baked.superiorsix.services.rounds import RoundService

_CUTOFF = timedelta(minutes=10)
_USER_CHOICES = {PredictionChoice.HOME, PredictionChoice.AWAY, PredictionChoice.DRAW}
# Calendar-day gap between consecutive selected fixtures that starts a new game week.
# Contiguous selected days within a ~week window stay one slate; ISO/Monday weeks are not used.
_GAME_WEEK_GAP_DAYS = 6


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

        selected = await self._matches.selected_matches()
        week_kickoff = earliest_kickoff_for_game_week(match, selected)
        cutoff_at = (now or datetime.now(UTC)) + _CUTOFF
        if cutoff_at >= week_kickoff:
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

    async def _require_current_round(self):
        try:
            return await self._rounds.current()
        except RoundNotFoundError as exc:
            raise PredictionError(str(exc)) from exc


def earliest_kickoff_for_game_week(match: Match, selected: list[Match]) -> datetime:
    """Earliest kickoff among selected fixtures in the same game week as ``match``.

    A game week is a cluster of Super Six selections (``use_match`` + set
    ``game_number``), sorted by kickoff. A new week starts when the calendar-day
    gap from the previous selected match exceeds ``_GAME_WEEK_GAP_DAYS`` (6).
    This is intentional slate clustering — not ISO/Monday weeks.
    """
    week = game_week_for_match(match, selected)
    if not week:
        return _as_utc(match.match_date)
    return min(_as_utc(fixture.match_date) for fixture in week)


def game_week_for_match(match: Match, selected: list[Match]) -> list[Match]:
    """Return the game-week cluster containing ``match``, or ``[]`` if none."""
    for week in cluster_game_weeks(selected):
        if any(fixture.id == match.id for fixture in week):
            return week
    return []


def cluster_game_weeks(selected: list[Match]) -> list[list[Match]]:
    """Partition Super Six selections into game weeks by kickoff day gaps.

    Only ``use_match=True`` fixtures with a set ``game_number`` are considered.
    Sorted by ``match_date``; a gap of more than 6 calendar days between
    consecutive selected fixtures starts a new week.
    """
    slate = [
        fixture
        for fixture in selected
        if fixture.use_match and fixture.game_number is not None
    ]
    slate.sort(key=lambda fixture: (_as_utc(fixture.match_date), fixture.id or 0))

    weeks: list[list[Match]] = []
    for fixture in slate:
        if not weeks:
            weeks.append([fixture])
            continue
        previous = weeks[-1][-1]
        gap = (_calendar_day(fixture.match_date) - _calendar_day(previous.match_date)).days
        if gap > _GAME_WEEK_GAP_DAYS:
            weeks.append([fixture])
        else:
            weeks[-1].append(fixture)
    return weeks


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _calendar_day(value: datetime) -> date:
    return _as_utc(value).date()


def _parse_user_choice(value: str) -> PredictionChoice:
    try:
        choice = PredictionChoice(value)
    except ValueError as exc:
        raise PredictionError("prediction must be one of: home, away, draw.") from exc
    if choice not in _USER_CHOICES:
        raise PredictionError("prediction must be one of: home, away, draw.")
    return choice
