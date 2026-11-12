from dataclasses import dataclass
from datetime import UTC, datetime

from baked.superiorsix.domain.exceptions import (
    RoundNotFoundError,
    SubroundError,
    SubroundNotFoundError,
)
from baked.superiorsix.domain.models import Match, Subround
from baked.superiorsix.repositories.matches import MatchRepository
from baked.superiorsix.repositories.subrounds import SubroundRepository
from baked.superiorsix.services.rounds import RoundService


@dataclass(frozen=True, slots=True)
class SubroundStatus:
    """API-facing subround view with kickoff bounds and open/closed flags."""

    subround: Subround
    match_count: int
    min_kickoff: datetime | None
    max_kickoff: datetime | None
    is_over: bool
    is_open_for_editing: bool


class SubroundService:
    def __init__(
        self,
        subrounds: SubroundRepository,
        matches: MatchRepository,
        rounds: RoundService,
    ) -> None:
        self._subrounds = subrounds
        self._matches = matches
        self._rounds = rounds

    async def get(self, subround_id: int) -> Subround:
        subround = await self._subrounds.get(subround_id)
        if subround is None or subround.id is None:
            raise SubroundNotFoundError(f"Subround {subround_id} not found.")
        return subround

    async def list_for_current_round(
        self,
        *,
        now: datetime | None = None,
    ) -> list[SubroundStatus]:
        """List subrounds for the current pot round.

        With no open round, returns ``[]``. Does not create Round or Subround —
        the first subround is created on ``addmatches`` submit.
        """
        round_ = await self._rounds.current_or_none()
        if round_ is None or round_.id is None:
            return []
        return await self.list_for_round(round_.id, now=now)

    async def list_for_round(
        self,
        round_id: int,
        *,
        now: datetime | None = None,
    ) -> list[SubroundStatus]:
        clock = now or datetime.now(UTC)
        subrounds = await self._subrounds.list_for_round(round_id)
        editing_id = await self._editing_subround_id(subrounds, clock)
        statuses: list[SubroundStatus] = []
        for subround in subrounds:
            assert subround.id is not None
            selected = await self._matches.list_selected_for_subround(subround.id)
            statuses.append(
                _status_for(
                    subround,
                    selected,
                    now=clock,
                    is_open_for_editing=subround.id == editing_id,
                )
            )
        return statuses

    async def current_open_for_editing(
        self,
        *,
        now: datetime | None = None,
    ) -> SubroundStatus | None:
        """Latest non-over subround for the current round, or ``None``.

        ``None`` when no parent round is open, when no subrounds exist yet, or
        when the latest subround is over. Does not create Round or Subround.
        """
        round_ = await self._rounds.current_or_none()
        if round_ is None or round_.id is None:
            return None
        clock = now or datetime.now(UTC)
        return await self._open_editing_for_round(round_.id, clock)

    async def latest_for_current_round(
        self,
        *,
        now: datetime | None = None,
    ) -> SubroundStatus | None:
        """Highest-sequence subround for the open parent round (even if over)."""
        round_ = await self._rounds.current_or_none()
        if round_ is None or round_.id is None:
            return None
        return await self.latest_status_for_round(round_.id, now=now)

    async def latest_status_for_round(
        self,
        round_id: int,
        *,
        now: datetime | None = None,
    ) -> SubroundStatus | None:
        clock = now or datetime.now(UTC)
        latest = await self._subrounds.latest_for_round(round_id)
        if latest is None or latest.id is None:
            return None
        return await self.status_for_id(latest.id, now=clock)

    async def status_for_id(
        self,
        subround_id: int,
        *,
        now: datetime | None = None,
    ) -> SubroundStatus:
        clock = now or datetime.now(UTC)
        subround = await self.get(subround_id)
        assert subround.id is not None
        selected = await self._matches.list_selected_for_subround(subround.id)
        editing = await self._open_editing_for_round(subround.round_id, clock)
        editing_id = None if editing is None else editing.subround.id
        return _status_for(
            subround,
            selected,
            now=clock,
            is_open_for_editing=subround.id == editing_id,
        )

    async def current_or_create_open(
        self,
        *,
        now: datetime | None = None,
    ) -> SubroundStatus:
        """Return the open editing subround, creating the next when none / over.

        Used by ``addmatches`` / ``create=true``. Requires an open parent Round
        (manual). Raises ``SubroundError`` when no round is in progress.
        """
        round_ = await self._require_current_round()
        assert round_.id is not None
        clock = now or datetime.now(UTC)
        existing = await self._open_editing_for_round(round_.id, clock)
        if existing is not None:
            return existing
        return await self.open_next(round_id=round_.id, now=clock, force=False)

    async def open_next(
        self,
        *,
        round_id: int | None = None,
        now: datetime | None = None,
        force: bool = True,
    ) -> SubroundStatus:
        """Create the next subround sequence for the (current) round.

        When ``force`` is False, returns the existing open editing subround if any.
        When ``force`` is True (admin explicit open), creates even if the previous
        slate is still before its max kickoff — previous stays prediction-open but
        is no longer the editing target.

        Requires an open parent Round — never creates one.
        """
        clock = now or datetime.now(UTC)
        if round_id is None:
            round_ = await self._require_current_round()
            round_id = round_.id
            assert round_id is not None
        else:
            await self._rounds.get(round_id)

        if not force:
            existing = await self._open_editing_for_round(round_id, clock)
            if existing is not None:
                return existing

        latest = await self._subrounds.latest_for_round(round_id)
        if latest is not None and latest.id is not None and latest.closed_at is None:
            selected = await self._matches.list_selected_for_subround(latest.id)
            if _is_over(selected, clock) and latest.closed_at is None:
                latest.closed_at = clock
                await self._subrounds.save(latest)

        sequence = await self._subrounds.next_sequence(round_id)
        created = await self._subrounds.add(
            Subround(round_id=round_id, sequence=sequence, created_at=clock)
        )
        assert created.id is not None
        return _status_for(created, [], now=clock, is_open_for_editing=True)

    async def delete_empty(
        self,
        subround_id: int,
        *,
        now: datetime | None = None,
    ) -> SubroundStatus | None:
        """Delete a subround that has no selected matches (SS-220).

        Refuses when the subround already has a slate. Returns the open-for-editing
        status for the parent round after delete (``None`` when none remain open).
        """
        clock = now or datetime.now(UTC)
        subround = await self.get(subround_id)
        assert subround.id is not None
        selected = await self._matches.list_selected_for_subround(subround.id)
        if selected:
            raise SubroundError("Cannot delete: subround has matches selected.")
        await self._subrounds.delete(subround.id)
        return await self._open_editing_for_round(subround.round_id, clock)

    async def require_open_for_selection(
        self,
        *,
        now: datetime | None = None,
    ) -> Subround:
        """Open-or-create the editing subround for admin match selection.

        Requires an open parent Round.
        """
        status = await self.current_or_create_open(now=now)
        assert status.subround.id is not None
        return status.subround

    async def _require_current_round(self):
        try:
            return await self._rounds.current()
        except RoundNotFoundError as exc:
            raise SubroundError("No round in progress.") from exc

    async def _open_editing_for_round(
        self,
        round_id: int,
        now: datetime,
    ) -> SubroundStatus | None:
        subrounds = await self._subrounds.list_for_round(round_id)
        editing_id = await self._editing_subround_id(subrounds, now)
        if editing_id is None:
            return None
        for status in await self.list_for_round(round_id, now=now):
            if status.subround.id == editing_id:
                return status
        return None

    async def _editing_subround_id(
        self,
        subrounds: list[Subround],
        now: datetime,
    ) -> int | None:
        """Highest-sequence subround that is not over (empty counts as not over)."""
        for subround in reversed(subrounds):
            assert subround.id is not None
            selected = await self._matches.list_selected_for_subround(subround.id)
            if not _is_over(selected, now):
                return subround.id
            break
        return None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _is_over(selected: list[Match], now: datetime) -> bool:
    """A subround is over when ``now >= max(kickoff)`` among its selected matches.

    Empty subrounds are not over (still open for editing / first submit).
    """
    if not selected:
        return False
    max_kickoff = max(_as_utc(match.match_date) for match in selected)
    return _as_utc(now) >= max_kickoff


def _status_for(
    subround: Subround,
    selected: list[Match],
    *,
    now: datetime,
    is_open_for_editing: bool,
) -> SubroundStatus:
    kickoffs = [_as_utc(match.match_date) for match in selected]
    return SubroundStatus(
        subround=subround,
        match_count=len(selected),
        min_kickoff=min(kickoffs) if kickoffs else None,
        max_kickoff=max(kickoffs) if kickoffs else None,
        is_over=_is_over(selected, now),
        is_open_for_editing=is_open_for_editing and not _is_over(selected, now),
    )
