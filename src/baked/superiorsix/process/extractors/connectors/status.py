from __future__ import annotations

from baked.superiorsix.domain.models import MatchStatus

_FOOTBALL_DATA_STATUS = {
    "SCHEDULED": MatchStatus.SCHEDULED,
    "TIMED": MatchStatus.SCHEDULED,
    "IN_PLAY": MatchStatus.IN_PLAY,
    # football-data PAUSED is the concrete half-time signal for 90-minute matches.
    "PAUSED": MatchStatus.HALF_TIME,
    "EXTRA_TIME": MatchStatus.IN_PLAY,
    "PENALTY_SHOOTOUT": MatchStatus.IN_PLAY,
    "FINISHED": MatchStatus.FINISHED,
    "AWARDED": MatchStatus.FINISHED,
    "POSTPONED": MatchStatus.POSTPONED,
    "SUSPENDED": MatchStatus.POSTPONED,
    "CANCELLED": MatchStatus.POSTPONED,
}

_GOAL_API_STATUS = {
    "SCHEDULED": MatchStatus.SCHEDULED,
    "LIVE": MatchStatus.IN_PLAY,
    "HALF_TIME": MatchStatus.HALF_TIME,
    "FINISHED": MatchStatus.FINISHED,
    "AFTER_ET": MatchStatus.FINISHED,
    "AFTER_PEN": MatchStatus.FINISHED,
    "AWARDED": MatchStatus.FINISHED,
    "POSTPONED": MatchStatus.POSTPONED,
    "CANCELLED": MatchStatus.POSTPONED,
    "ABANDONED": MatchStatus.POSTPONED,
    "SUSPENDED": MatchStatus.POSTPONED,
}


def map_football_data_status(raw: str | None) -> str:
    if not raw:
        return MatchStatus.SCHEDULED
    return _FOOTBALL_DATA_STATUS.get(raw.upper(), MatchStatus.SCHEDULED)


def map_goal_api_status(raw: str | None) -> str:
    if not raw:
        return MatchStatus.SCHEDULED
    return _GOAL_API_STATUS.get(raw.upper(), MatchStatus.SCHEDULED)
