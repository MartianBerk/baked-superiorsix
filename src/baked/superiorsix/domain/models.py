from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from uuid import UUID


class MatchStatus(StrEnum):
    SCHEDULED = "SCHEDULED"
    POSTPONED = "POSTPONED"
    IN_PLAY = "IN PLAY"
    HALF_TIME = "HALF TIME"
    FINISHED = "FINISHED"


class TeamResult(StrEnum):
    WIN = "WIN"
    LOSE = "LOSE"
    DRAW = "DRAW"


class PredictionChoice(StrEnum):
    HOME = "home"
    AWAY = "away"
    DRAW = "draw"
    VOID = "void"


@dataclass(slots=True)
class League:
    name: str
    code: str
    start_date: date
    id: int | None = None


@dataclass(frozen=True, slots=True)
class TeamXref:
    """Provider team name → canonical xref (display + stats identity)."""

    team_name: str
    xref: str


@dataclass(slots=True)
class Match:
    external_id: str
    league_id: int
    match_date: datetime
    status: str
    home_team: str
    away_team: str
    id: int | None = None
    match_minute: int | None = None
    use_match: bool = False
    home_score: int | None = None
    away_score: int | None = None
    game_number: int | None = None
    last_updated: datetime | None = None
    subround_id: int | None = None
    # Persisted prediction difficulty (SS-125). Once both are set they are immutable.
    difficulty_score: float | None = None
    difficulty_band: int | None = None
    # Populated from leagues.code when loaded via repository join (not persisted).
    league_code: str | None = None


@dataclass(frozen=True, slots=True)
class LeagueTableEntry:
    league: str
    season: str
    team: str
    matches_played: int
    matches_won: int
    matches_drawn: int
    matches_lost: int
    goals_for: int
    goals_against: int
    goal_difference: int
    points: int
    position: str = ""


@dataclass(frozen=True, slots=True)
class MatchDifficultyScore:
    """Prediction difficulty attached to match detail / admin match payloads."""

    score: float
    band: int


@dataclass(frozen=True, slots=True)
class MatchDetail:
    """Form / H2H / table context for one fixture.

    ``home_team`` / ``away_team`` are provider spellings stored on the match row.
    ``home_team_xref`` / ``away_team_xref`` are display labels (team_xref, or the
    provider name when unmapped). Form/H2H/position dicts are keyed by xref.
    """

    id: int
    home_team: str
    away_team: str
    home_team_xref: str
    away_team_xref: str
    league_position: dict[str, str | None]
    team_performance: dict[str, list[TeamResult]]
    head_to_head: dict[str, list[TeamResult]]
    difficulty: MatchDifficultyScore | None = None


@dataclass(slots=True)
class Round:
    start_date: date
    buy_in_pence: int
    id: int | None = None
    end_date: date | None = None
    amount_won: int | None = None
    n_players: int | None = None
    winner_ids: list[UUID] | None = None


@dataclass(slots=True)
class Subround:
    """One admin match-submission slate under a parent pot/round."""

    round_id: int
    sequence: int
    id: int | None = None
    created_at: datetime | None = None
    closed_at: datetime | None = None


@dataclass(slots=True)
class Prediction:
    round_id: int
    auth_id: UUID
    match_id: int
    prediction: str
    id: int | None = None
    dropped: bool = False


@dataclass(slots=True)
class EurosTournament:
    start_date: date
    buy_in_pence: int
    id: int | None = None
    competition_code: str = "EC"
    label: str = "European Championship"
    end_date: date | None = None
    entrant_count: int = 0
    winner_ids: list[UUID] | None = None
    created_at: datetime | None = None


@dataclass(slots=True)
class EurosMatch:
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
class EurosPrediction:
    auth_id: UUID
    match_id: int
    prediction: str
    id: int | None = None
    plus_ninety: bool = False
    extra_time: bool = False
    penalties: bool = False
    dropped: bool = False


@dataclass(frozen=True, slots=True)
class EurosScoreRow:
    """Legacy ``EUROS_SCORES`` row shape (player display name + totals)."""

    player: str
    score: int
    bonus: int
    total: int
    auth_id: UUID | None = None
