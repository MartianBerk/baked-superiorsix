from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from uuid import UUID


@dataclass(frozen=True, slots=True)
class AuthPrincipal:
    """Identity taken from a verified baked-auth access token."""

    auth_id: UUID
    username: str | None = None
    roles: tuple[str, ...] = ()


class MatchStatus(StrEnum):
    SCHEDULED = "SCHEDULED"
    POSTPONED = "POSTPONED"
    IN_PLAY = "IN PLAY"
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
    current_matchday: int | None = None


@dataclass(slots=True)
class Match:
    external_id: str
    league_id: int
    matchday: int
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
class MatchDetail:
    league_position: dict[str, str | None]
    team_performance: dict[str, list[TeamResult]]
    head_to_head: dict[str, list[TeamResult]]


@dataclass(slots=True)
class Round:
    start_date: date
    buy_in_pence: int
    id: int | None = None
    end_date: date | None = None


@dataclass(slots=True)
class Prediction:
    round_id: int
    auth_id: UUID
    match_id: int
    prediction: str
    id: int | None = None
    dropped: bool = False
