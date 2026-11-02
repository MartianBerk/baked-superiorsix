from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True, slots=True)
class LeaguePayload:
    name: str
    code: str
    start_date: date
    current_matchday: int | None = None


@dataclass(frozen=True, slots=True)
class MatchPayload:
    external_id: str
    matchday: int
    match_date: datetime
    status: str
    home_team: str
    away_team: str
    home_score: int | None = None
    away_score: int | None = None
    match_minute: int | None = None
