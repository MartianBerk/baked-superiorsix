"""Unit tests for derived match-minute clock math (SS-122)."""

from datetime import UTC, datetime, timedelta

import pytest

from baked.superiorsix.domain.match_clock import compute_match_minute, resolve_match_minute
from baked.superiorsix.domain.models import Match, MatchStatus

KICKOFF = datetime(2026, 11, 7, 15, 0, tzinfo=UTC)


def _at(*, minutes: int, seconds: int = 0) -> datetime:
    return KICKOFF + timedelta(minutes=minutes, seconds=seconds)


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (_at(minutes=0, seconds=0), 1),
        (_at(minutes=0, seconds=59), 1),
        (_at(minutes=12), 13),
        (_at(minutes=35), 36),
        (_at(minutes=44, seconds=59), 45),
    ],
)
def test_first_half_minutes(now: datetime, expected: int) -> None:
    assert (
        compute_match_minute(
            kickoff=KICKOFF,
            status=MatchStatus.IN_PLAY,
            now=now,
        )
        == expected
    )


@pytest.mark.parametrize(
    "now",
    [
        _at(minutes=45),
        _at(minutes=45, seconds=1),
        _at(minutes=52),
        _at(minutes=59, seconds=59),
    ],
)
def test_half_time_window_is_null_while_in_play(now: datetime) -> None:
    assert (
        compute_match_minute(
            kickoff=KICKOFF,
            status=MatchStatus.IN_PLAY,
            now=now,
        )
        is None
    )


def test_half_time_status_is_null_even_outside_window() -> None:
    assert (
        compute_match_minute(
            kickoff=KICKOFF,
            status=MatchStatus.HALF_TIME,
            now=_at(minutes=70),
        )
        is None
    )


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (_at(minutes=60), 46),
        (_at(minutes=60, seconds=30), 46),
        (_at(minutes=67), 53),
        (_at(minutes=104, seconds=59), 90),
    ],
)
def test_second_half_minutes(now: datetime, expected: int) -> None:
    assert (
        compute_match_minute(
            kickoff=KICKOFF,
            status=MatchStatus.IN_PLAY,
            now=now,
        )
        == expected
    )


def test_past_regulation_while_in_play_caps_at_ninety() -> None:
    assert (
        compute_match_minute(
            kickoff=KICKOFF,
            status=MatchStatus.IN_PLAY,
            now=_at(minutes=120),
        )
        == 90
    )


def test_finished_defaults_to_ninety() -> None:
    assert (
        compute_match_minute(
            kickoff=KICKOFF,
            status=MatchStatus.FINISHED,
            now=_at(minutes=120),
        )
        == 90
    )


def test_scheduled_and_postponed_stay_null() -> None:
    now = _at(minutes=10)
    assert (
        compute_match_minute(kickoff=KICKOFF, status=MatchStatus.SCHEDULED, now=now)
        is None
    )
    assert (
        compute_match_minute(kickoff=KICKOFF, status=MatchStatus.POSTPONED, now=now)
        is None
    )


def test_before_kickoff_while_in_play_is_null() -> None:
    assert (
        compute_match_minute(
            kickoff=KICKOFF,
            status=MatchStatus.IN_PLAY,
            now=KICKOFF - timedelta(minutes=1),
        )
        is None
    )


def test_provider_minute_wins_over_computation() -> None:
    assert (
        compute_match_minute(
            kickoff=KICKOFF,
            status=MatchStatus.IN_PLAY,
            provider_minute=67,
            now=_at(minutes=10),
        )
        == 67
    )
    assert (
        compute_match_minute(
            kickoff=KICKOFF,
            status=MatchStatus.HALF_TIME,
            provider_minute=45,
            now=_at(minutes=50),
        )
        == 45
    )
    assert (
        compute_match_minute(
            kickoff=KICKOFF,
            status=MatchStatus.FINISHED,
            provider_minute=90,
            now=_at(minutes=120),
        )
        == 90
    )


def test_naive_datetimes_treated_as_utc() -> None:
    naive_kickoff = datetime(2026, 11, 7, 15, 0)
    naive_now = datetime(2026, 11, 7, 15, 20)
    assert (
        compute_match_minute(
            kickoff=naive_kickoff,
            status=MatchStatus.IN_PLAY,
            now=naive_now,
        )
        == 21
    )


def test_resolve_match_minute_uses_match_fields() -> None:
    match = Match(
        external_id="fd-1",
        league_id=1,
        match_date=KICKOFF,
        status=MatchStatus.IN_PLAY,
        home_team="Home",
        away_team="Away",
        match_minute=None,
    )
    assert resolve_match_minute(match, now=_at(minutes=22)) == 23

    match.match_minute = 41
    assert resolve_match_minute(match, now=_at(minutes=22)) == 41
