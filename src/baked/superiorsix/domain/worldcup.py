"""World Cup domain types and scoring (legacy WORLDCUP_* rules)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from baked.superiorsix.domain.models import PredictionChoice

# Knockout bonus applies from matchday 4 upward (legacy WORLDCUP_SCORES view).
KNOCKOUT_MATCHDAY_FLOOR = 4


@dataclass(slots=True)
class WorldcupMatch:
    external_id: str
    league_id: int
    matchday: int
    match_date: datetime
    status: str
    home_team: str
    away_team: str
    id: int | None = None
    match_minute: int | None = None
    use_match: bool = True
    home_score: int | None = None
    away_score: int | None = None
    extra_time: bool = False
    penalties: bool = False
    last_updated: datetime | None = None


@dataclass(slots=True)
class WorldcupPrediction:
    auth_id: UUID
    match_id: int
    prediction: str
    id: int | None = None
    plus_ninety: bool = False
    extra_time: bool = False
    penalties: bool = False
    dropped: bool = False


@dataclass(slots=True)
class WorldcupSettings:
    """Static tournament pot — buy-in fixed at start; no round rollover."""

    buy_in_pence: int
    participant_count: int
    id: int = 1
    open: bool = False
    winner_ids: list[UUID] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class WorldcupScoreRow:
    auth_id: UUID
    player: str
    score: int
    bonus: int
    total: int


def prediction_result_point(
    *,
    prediction: str,
    home_score: int | None,
    away_score: int | None,
) -> int:
    """1 if the home/away/draw pick matches the finished result, else 0."""
    if home_score is None or away_score is None:
        return 0
    choice = prediction.strip().lower()
    if choice == PredictionChoice.HOME and home_score > away_score:
        return 1
    if choice == PredictionChoice.AWAY and away_score > home_score:
        return 1
    if choice == PredictionChoice.DRAW and home_score == away_score:
        return 1
    return 0


def knockout_bonus_points(
    *,
    matchday: int,
    plus_ninety: bool,
    predicted_extra_time: bool,
    predicted_penalties: bool,
    match_extra_time: bool,
    match_penalties: bool,
) -> int:
    """Legacy knockout bonus (only meaningful when the result pick is correct).

    * matchday < 4 → 0 (group stage)
    * pens predicted and match went to pens → 3
    * ET predicted and match went to ET → 2
    * neither predicted and match had neither → 1
    * else → 0
    """
    if matchday < KNOCKOUT_MATCHDAY_FLOOR:
        return 0
    if plus_ninety and predicted_penalties and match_penalties:
        return 3
    if plus_ninety and predicted_extra_time and match_extra_time:
        return 2
    if (not plus_ninety) and (not match_extra_time) and (not match_penalties):
        return 1
    return 0


def score_prediction_against_match(
    prediction: WorldcupPrediction,
    match: WorldcupMatch,
) -> tuple[int, int]:
    """Return ``(score, bonus)`` for one finished match prediction.

    Bonus is zeroed when the result pick is wrong (legacy view fold).
    """
    if match.status != "FINISHED":
        return 0, 0
    score = prediction_result_point(
        prediction=prediction.prediction,
        home_score=match.home_score,
        away_score=match.away_score,
    )
    raw_bonus = knockout_bonus_points(
        matchday=match.matchday,
        plus_ninety=prediction.plus_ninety,
        predicted_extra_time=prediction.extra_time,
        predicted_penalties=prediction.penalties,
        match_extra_time=bool(match.extra_time),
        match_penalties=bool(match.penalties),
    )
    bonus = raw_bonus if score > 0 else 0
    return score, bonus


def static_pot_pence(*, buy_in_pence: int, participant_count: int) -> int:
    """Static tournament pot: ``buy_in × participants`` (no round rollover)."""
    if buy_in_pence < 0 or participant_count < 0:
        return 0
    return buy_in_pence * participant_count
