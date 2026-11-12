"""Automated Super Six slate selection (SS-229 / SS-225).

Gates + same-kickoff hard rule + league hierarchy + target difficulty pips.
Pure helpers stay free of I/O so unit tests can cover selection without a DB.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

from baked.superiorsix.domain.exceptions import MatchSelectionError, SubroundError
from baked.superiorsix.domain.models import Match, MatchDifficultyScore, MatchStatus, Subround
from baked.superiorsix.services.gameday import prediction_is_correct
from baked.superiorsix.services.matches import MatchService
from baked.superiorsix.services.predictions import PredictionService
from baked.superiorsix.services.rounds import RoundService
from baked.superiorsix.services.subrounds import SubroundService, SubroundStatus

UK_TZ = ZoneInfo("Europe/London")

# League hierarchy wins over difficulty (product rules).
LEAGUE_HIERARCHY: tuple[str, ...] = ("PL", "ELC", "EL1", "EL2")
_LEAGUE_RANK = {code: index for index, code in enumerate(LEAGUE_HIERARCHY)}

# Known difficulty-round pointers (subround sequence → target pip). Interpolate between.
DIFFICULTY_POINTERS: tuple[tuple[int, float], ...] = (
    (1, 5.0),
    (15, 4.0),
    (25, 3.0),
    (40, 2.0),
    (50, 1.0),
)

SLATE_SIZE = 6
PREFERRED_KICKOFF_TIME = time(15, 0)


class AutopickReason(StrEnum):
    NO_OPEN_ROUND = "no_open_round"
    PREVIOUS_SUBROUND_MISSING = "previous_subround_missing"
    PREVIOUS_SUBROUND_NOT_CLOSED = "previous_subround_not_closed"
    PERFECT_SLATE_EXISTS = "perfect_slate_exists"
    INSUFFICIENT_SAME_KICKOFF = "insufficient_same_kickoff"
    SLATE_ALREADY_COMPLETE = "slate_already_complete"


_NOOP_MESSAGES: dict[AutopickReason, str] = {
    AutopickReason.NO_OPEN_ROUND: "No round in progress; autopick skipped.",
    AutopickReason.PREVIOUS_SUBROUND_MISSING: (
        "No previous subround to evaluate; autopick does not seed the first slate."
    ),
    AutopickReason.PREVIOUS_SUBROUND_NOT_CLOSED: (
        "Previous subround is not fully finished; autopick skipped."
    ),
    AutopickReason.PERFECT_SLATE_EXISTS: (
        "A perfect slate already exists on the previous subround; end the round instead."
    ),
    AutopickReason.INSUFFICIENT_SAME_KICKOFF: (
        "Not enough eligible fixtures share one kickoff on the selected day."
    ),
    AutopickReason.SLATE_ALREADY_COMPLETE: (
        "Editing subround already has a full slate of 6; autopick skipped."
    ),
}


@dataclass(frozen=True, slots=True)
class ScoredFixture:
    """Candidate fixture with difficulty band and league code for ranking."""

    match: Match
    band: int
    score: float
    league_code: str


@dataclass(frozen=True, slots=True)
class AutopickResult:
    """Outcome of an autopick run (success or expected no-op)."""

    status: str  # "selected" | "noop"
    reason: AutopickReason | None
    message: str
    subround: Subround | None
    subround_status: SubroundStatus | None
    matches: list[Match]
    kickoff: datetime | None
    target_difficulty_band: float | None
    average_difficulty_band: float | None

    @classmethod
    def noop(
        cls,
        reason: AutopickReason,
        *,
        message: str | None = None,
        subround: Subround | None = None,
        subround_status: SubroundStatus | None = None,
    ) -> AutopickResult:
        return cls(
            status="noop",
            reason=reason,
            message=message or _NOOP_MESSAGES[reason],
            subround=subround,
            subround_status=subround_status,
            matches=[],
            kickoff=None,
            target_difficulty_band=None,
            average_difficulty_band=None,
        )

    @classmethod
    def selected(
        cls,
        *,
        matches: list[Match],
        subround: Subround,
        subround_status: SubroundStatus,
        kickoff: datetime,
        target_difficulty_band: float,
        message: str | None = None,
    ) -> AutopickResult:
        bands = [
            match.difficulty_band
            for match in matches
            if match.difficulty_band is not None
        ]
        average = (sum(bands) / len(bands)) if bands else None
        sequence = subround.sequence
        return cls(
            status="selected",
            reason=None,
            message=message
            or f"Selected {len(matches)} matches for subround {sequence}.",
            subround=subround,
            subround_status=subround_status,
            matches=matches,
            kickoff=kickoff,
            target_difficulty_band=target_difficulty_band,
            average_difficulty_band=None if average is None else round(average, 3),
        )


def target_difficulty_band(sequence: int) -> float:
    """Interpolate target pip difficulty for a subround sequence number."""
    if sequence <= DIFFICULTY_POINTERS[0][0]:
        return DIFFICULTY_POINTERS[0][1]
    if sequence >= DIFFICULTY_POINTERS[-1][0]:
        return DIFFICULTY_POINTERS[-1][1]
    for (left_seq, left_pip), (right_seq, right_pip) in zip(
        DIFFICULTY_POINTERS,
        DIFFICULTY_POINTERS[1:],
        strict=False,
    ):
        if left_seq <= sequence <= right_seq:
            if right_seq == left_seq:
                return left_pip
            ratio = (sequence - left_seq) / (right_seq - left_seq)
            return round(left_pip + (right_pip - left_pip) * ratio, 6)
    return DIFFICULTY_POINTERS[-1][1]


def next_saturday(on_day: date) -> date:
    """Next available Saturday on or after ``on_day`` (UK calendar day)."""
    days_ahead = (5 - on_day.weekday()) % 7
    return on_day + timedelta(days=days_ahead)


def uk_local(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UK_TZ)


def is_preferred_1500(kickoff: datetime) -> bool:
    """True when kickoff is 15:00 UK local (any weekday)."""
    local = uk_local(kickoff)
    return (
        local.hour == PREFERRED_KICKOFF_TIME.hour
        and local.minute == PREFERRED_KICKOFF_TIME.minute
    )


def is_preferred_saturday_1500(kickoff: datetime) -> bool:
    local = uk_local(kickoff)
    return local.weekday() == 5 and is_preferred_1500(kickoff)


def group_by_kickoff(matches: list[Match]) -> dict[datetime, list[Match]]:
    groups: dict[datetime, list[Match]] = {}
    for match in matches:
        key = match.match_date if match.match_date.tzinfo else match.match_date.replace(tzinfo=UTC)
        key = key.astimezone(UTC)
        groups.setdefault(key, []).append(match)
    return groups


def choose_kickoff_group(
    groups: dict[datetime, list[Match]],
    *,
    day: date,
) -> datetime | None:
    """Pick one shared kickoff on UK calendar ``day`` with ≥6 fixtures.

    Prefer 15:00 UK when that group qualifies; otherwise the largest
    qualifying group on that day (earliest kickoff as tie-break).
    """
    qualifying: list[tuple[datetime, list[Match]]] = []
    for kickoff, fixtures in groups.items():
        local = uk_local(kickoff)
        if local.date() != day:
            continue
        if len(fixtures) < SLATE_SIZE:
            continue
        qualifying.append((kickoff, fixtures))
    if not qualifying:
        return None

    preferred = [kickoff for kickoff, _ in qualifying if is_preferred_1500(kickoff)]
    if preferred:
        return preferred[0]

    qualifying.sort(key=lambda item: (-len(item[1]), item[0]))
    return qualifying[0][0]


def select_six_hierarchy_then_difficulty(
    scored: list[ScoredFixture],
    *,
    target_band: float,
) -> list[ScoredFixture]:
    """Fill six slots: league hierarchy first, then closest to target pip."""
    by_league: dict[str, list[ScoredFixture]] = {code: [] for code in LEAGUE_HIERARCHY}
    for item in scored:
        code = item.league_code.upper()
        if code in by_league:
            by_league[code].append(item)

    selected: list[ScoredFixture] = []
    for code in LEAGUE_HIERARCHY:
        remaining = SLATE_SIZE - len(selected)
        if remaining <= 0:
            break
        pool = by_league[code]
        if not pool:
            continue
        ranked = sorted(
            pool,
            key=lambda item: (
                abs(item.band - target_band),
                -item.band,  # prefer harder on equal distance
                item.match.id or 0,
            ),
        )
        selected.extend(ranked[:remaining])
    return selected


def _eligible(match: Match) -> bool:
    return match.status != MatchStatus.POSTPONED and not match.use_match


class AutopickService:
    """Orchestrates gates, candidate scoring, and slate persistence."""

    def __init__(
        self,
        *,
        rounds: RoundService,
        subrounds: SubroundService,
        matches: MatchService,
        predictions: PredictionService,
    ) -> None:
        self._rounds = rounds
        self._subrounds = subrounds
        self._matches = matches
        self._predictions = predictions

    async def run(
        self,
        *,
        now: datetime | None = None,
        match_date: date | None = None,
    ) -> AutopickResult:
        """Run autopick gates + selection.

        ``match_date`` (admin API): only consider that UK calendar day — no
        look-ahead. ``None`` (CLI / orchestration): look ahead to the next
        available Saturday from ``now``.
        """
        clock = now or datetime.now(UTC)
        round_ = await self._rounds.current_or_none()
        if round_ is None or round_.id is None:
            return AutopickResult.noop(AutopickReason.NO_OPEN_ROUND)

        statuses = await self._subrounds.list_for_round(round_.id, now=clock)
        previous = _previous_subround_for_gates(statuses)
        if previous is None:
            return AutopickResult.noop(AutopickReason.PREVIOUS_SUBROUND_MISSING)

        previous_matches = await self._matches.selected_matches_for_subround(
            previous.subround.id  # type: ignore[arg-type]
        )
        if not previous_matches or not all(
            match.status == MatchStatus.FINISHED for match in previous_matches
        ):
            return AutopickResult.noop(AutopickReason.PREVIOUS_SUBROUND_NOT_CLOSED)

        if await self._has_perfect_slate(
            round_id=round_.id,
            matches=previous_matches,
        ):
            return AutopickResult.noop(AutopickReason.PERFECT_SLATE_EXISTS)

        editing = await self._subrounds.current_open_for_editing(now=clock)
        if editing is not None and editing.match_count >= SLATE_SIZE:
            return AutopickResult.noop(
                AutopickReason.SLATE_ALREADY_COMPLETE,
                subround=editing.subround,
                subround_status=editing,
            )

        if match_date is None:
            target_day = next_saturday(uk_local(clock).date())
        else:
            target_day = match_date

        # UK calendar day window in UTC (may span two UTC days).
        window_start = datetime.combine(target_day, time.min, tzinfo=UK_TZ).astimezone(UTC)
        window_end = window_start + timedelta(days=1)
        candidates = [
            match
            for match in await self._matches.list_in_range(start=window_start, end=window_end)
            if _eligible(match)
        ]
        groups = group_by_kickoff(candidates)
        kickoff = choose_kickoff_group(groups, day=target_day)
        if kickoff is None:
            return AutopickResult.noop(AutopickReason.INSUFFICIENT_SAME_KICKOFF)

        pool = groups[kickoff]
        difficulties = await self._matches.difficulties_for_matches(pool)
        scored: list[ScoredFixture] = []
        for match in pool:
            assert match.id is not None
            difficulty = difficulties.get(match.id)
            if difficulty is None:
                difficulty = MatchDifficultyScore(score=0.5, band=3)
            league_code = (match.league_code or "").upper()
            if league_code not in _LEAGUE_RANK:
                continue
            scored.append(
                ScoredFixture(
                    match=match,
                    band=difficulty.band,
                    score=difficulty.score,
                    league_code=league_code,
                )
            )

        if len(scored) < SLATE_SIZE:
            return AutopickResult.noop(AutopickReason.INSUFFICIENT_SAME_KICKOFF)

        # Next sequence = previous.sequence + 1 when creating; else editing.sequence.
        next_sequence = (
            editing.subround.sequence
            if editing is not None
            else previous.subround.sequence + 1
        )
        target = target_difficulty_band(next_sequence)
        picked = select_six_hierarchy_then_difficulty(scored, target_band=target)
        if len(picked) < SLATE_SIZE:
            return AutopickResult.noop(AutopickReason.INSUFFICIENT_SAME_KICKOFF)

        try:
            subround = await self._subrounds.require_open_for_selection(now=clock)
        except SubroundError:
            return AutopickResult.noop(AutopickReason.NO_OPEN_ROUND)
        assert subround.id is not None

        # Persist on the UTC calendar day of the shared kickoff (matches addmatches).
        day = kickoff.astimezone(UTC).date()
        selections = [(item.match.id, index) for index, item in enumerate(picked, start=1)]
        assert all(match_id is not None for match_id, _ in selections)
        try:
            selected = await self._matches.replace_day_selections(
                day,
                [(int(match_id), game_number) for match_id, game_number in selections],
                subround_id=subround.id,
            )
        except MatchSelectionError as exc:
            raise MatchSelectionError(str(exc)) from exc

        status = await self._subrounds.status_for_id(subround.id, now=clock)
        return AutopickResult.selected(
            matches=selected,
            subround=subround,
            subround_status=status,
            kickoff=kickoff,
            target_difficulty_band=target,
        )

    async def _has_perfect_slate(self, *, round_id: int, matches: list[Match]) -> bool:
        match_ids = [match.id for match in matches if match.id is not None]
        if not match_ids:
            return False
        predictions = await self._predictions.list_active_for_matches(
            round_id=round_id,
            match_ids=match_ids,
        )
        by_auth: dict[object, dict[int, str]] = {}
        for prediction in predictions:
            by_auth.setdefault(prediction.auth_id, {})[prediction.match_id] = (
                prediction.prediction
            )
        slate_size = len(match_ids)
        for picks in by_auth.values():
            correct = 0
            for match in matches:
                assert match.id is not None
                choice = picks.get(match.id)
                if choice is not None and prediction_is_correct(match, choice):
                    correct += 1
            if correct == slate_size:
                return True
        return False


def _previous_subround_for_gates(
    statuses: list[SubroundStatus],
) -> SubroundStatus | None:
    """Resolve the subround whose finished state gates the next autopick.

    Empty latest (force-opened / pending edit) → evaluate the one before it.
    Otherwise evaluate the latest slate that has matches.
    """
    if not statuses:
        return None
    latest = statuses[-1]
    if latest.match_count == 0:
        if len(statuses) < 2:
            return None
        previous = statuses[-2]
        return previous if previous.match_count > 0 else None
    return latest
