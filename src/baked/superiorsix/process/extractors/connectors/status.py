from __future__ import annotations

from baked.superiorsix.domain.models import MatchStatus

_FOOTBALL_DATA_STATUS = {
    "SCHEDULED": MatchStatus.SCHEDULED,
    "TIMED": MatchStatus.SCHEDULED,
    "IN_PLAY": MatchStatus.IN_PLAY,
    "PAUSED": MatchStatus.IN_PLAY,
    "EXTRA_TIME": MatchStatus.IN_PLAY,
    "PENALTY_SHOOTOUT": MatchStatus.IN_PLAY,
    "FINISHED": MatchStatus.FINISHED,
    "AWARDED": MatchStatus.FINISHED,
    "POSTPONED": MatchStatus.POSTPONED,
    "SUSPENDED": MatchStatus.POSTPONED,
    "CANCELLED": MatchStatus.POSTPONED,
}

_API_FOOTBALL_STATUS = {
    "NS": MatchStatus.SCHEDULED,
    "TBD": MatchStatus.SCHEDULED,
    "1H": MatchStatus.IN_PLAY,
    "HT": MatchStatus.IN_PLAY,
    "2H": MatchStatus.IN_PLAY,
    "ET": MatchStatus.IN_PLAY,
    "BT": MatchStatus.IN_PLAY,
    "P": MatchStatus.IN_PLAY,
    "LIVE": MatchStatus.IN_PLAY,
    "FT": MatchStatus.FINISHED,
    "AET": MatchStatus.FINISHED,
    "PEN": MatchStatus.FINISHED,
    "AWD": MatchStatus.FINISHED,
    "WO": MatchStatus.FINISHED,
    "PST": MatchStatus.POSTPONED,
    "SUSP": MatchStatus.POSTPONED,
    "CANC": MatchStatus.POSTPONED,
    "ABD": MatchStatus.POSTPONED,
}


def map_football_data_status(raw: str | None) -> str:
    if not raw:
        return MatchStatus.SCHEDULED
    return _FOOTBALL_DATA_STATUS.get(raw.upper(), MatchStatus.SCHEDULED)


def map_api_football_status(raw: str | None) -> str:
    if not raw:
        return MatchStatus.SCHEDULED
    return _API_FOOTBALL_STATUS.get(raw.upper(), MatchStatus.SCHEDULED)
