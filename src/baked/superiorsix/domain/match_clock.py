"""Derived match clock when a provider does not supply ``match_minute``.

football-data.org live payloads often omit minute; Goal API usually includes it.
Display rules (ignore added time):

* First 45′ wall from kickoff → first-half minutes ``1..45``
* Next 15′ wall → half-time pause → ``None`` (FE treats ``IN PLAY`` + null as HT)
* Next 45′ wall → second-half minutes ``46..90``
* ``FINISHED`` → ``90`` when provider minute is absent
* Prefer a non-null provider minute always
"""

from __future__ import annotations

from datetime import UTC, datetime

from baked.superiorsix.domain.models import Match, MatchStatus

_FIRST_HALF_MINUTES = 45
_HALF_TIME_MINUTES = 15
_HALF_TIME_END_WALL = _FIRST_HALF_MINUTES + _HALF_TIME_MINUTES  # 60
_SECOND_HALF_END_WALL = _HALF_TIME_END_WALL + _FIRST_HALF_MINUTES  # 105
_FULL_TIME_MINUTE = 90


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def compute_match_minute(
    *,
    kickoff: datetime,
    status: str,
    provider_minute: int | None = None,
    now: datetime | None = None,
) -> int | None:
    """Resolve the minute shown on livescores / match payloads.

    Provider minutes win. Otherwise derive from kickoff + ``now`` while the
    match is in play, leave ``None`` during the modelled HT window, and use
    ``90`` for finished fixtures.
    """
    if provider_minute is not None:
        return provider_minute

    if status == MatchStatus.FINISHED:
        return _FULL_TIME_MINUTE
    if status == MatchStatus.HALF_TIME:
        return None
    if status != MatchStatus.IN_PLAY:
        return None

    clock = _as_utc(now or datetime.now(UTC))
    start = _as_utc(kickoff)
    elapsed_minutes = int((clock - start).total_seconds() // 60)

    if elapsed_minutes < 0:
        return None
    if elapsed_minutes < _FIRST_HALF_MINUTES:
        # Current minute of play (1-based), capped at 45′ (no added time).
        return min(elapsed_minutes + 1, _FIRST_HALF_MINUTES)
    if elapsed_minutes < _HALF_TIME_END_WALL:
        # 15′ HT pause — keep null so FE ``IN PLAY``+null → HT still works
        # when status lags as ``IN PLAY``.
        return None
    if elapsed_minutes < _SECOND_HALF_END_WALL:
        # Second half: wall 60′ → 46′, …, wall 104′ → 90′.
        return min(elapsed_minutes - _HALF_TIME_MINUTES + 1, _FULL_TIME_MINUTE)
    return _FULL_TIME_MINUTE


def resolve_match_minute(match: Match, *, now: datetime | None = None) -> int | None:
    """Effective ``match_minute`` for API serialization of a domain match."""
    return compute_match_minute(
        kickoff=match.match_date,
        status=match.status,
        provider_minute=match.match_minute,
        now=now,
    )
