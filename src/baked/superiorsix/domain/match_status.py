"""Forward-only match status transitions for ingest upserts / score updates.

Lifecycle (higher rank wins; equal ranks may move sideways):

* ``SCHEDULED`` / ``POSTPONED`` (rank 0) → ``IN PLAY`` / ``HALF TIME`` (rank 1)
  → ``FINISHED`` (rank 2, terminal).
* ``IN PLAY`` ↔ ``HALF TIME`` is allowed (same rank).
* ``SCHEDULED`` ↔ ``POSTPONED`` is allowed (same rank; reschedule / postpone).
* Never regress, e.g. ``FINISHED`` → ``IN PLAY`` / ``SCHEDULED`` / ``POSTPONED``,
  or ``IN PLAY`` → ``SCHEDULED``.
"""

from __future__ import annotations

from baked.superiorsix.domain.models import MatchStatus

# Higher = further along the match lifecycle. Equal ranks are interchangeable.
_STATUS_RANK: dict[str, int] = {
    MatchStatus.SCHEDULED: 0,
    MatchStatus.POSTPONED: 0,
    MatchStatus.IN_PLAY: 1,
    MatchStatus.HALF_TIME: 1,
    MatchStatus.FINISHED: 2,
}


def status_rank(status: str) -> int | None:
    """Return the lifecycle rank for a known status, else ``None``."""
    return _STATUS_RANK.get(status)


def is_status_regression(current: str, incoming: str) -> bool:
    """True when ``incoming`` would move the match backward along the lifecycle."""
    if current == incoming:
        return False
    current_rank = status_rank(current)
    incoming_rank = status_rank(incoming)
    if current_rank is None or incoming_rank is None:
        return False
    return incoming_rank < current_rank


def coalesce_forward_status(current: str, incoming: str) -> str:
    """Choose the status to persist: keep ``current`` when ``incoming`` regresses."""
    if is_status_regression(current, incoming):
        return current
    return incoming
