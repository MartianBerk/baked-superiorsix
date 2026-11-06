"""Unit tests for pure match-difficulty scoring (SS-79)."""

from baked.superiorsix.domain.models import TeamResult
from baked.superiorsix.services.difficulty import (
    WEIGHT_HEAD_TO_HEAD,
    WEIGHT_LEAGUE_POSITION,
    WEIGHT_PERFORMANCE,
    band_for_score,
    calculate_match_difficulty,
    closeness,
    form_strength,
    parse_league_position,
    position_strength,
)

W = TeamResult.WIN
D = TeamResult.DRAW
L = TeamResult.LOSE


def test_parse_league_position() -> None:
    assert parse_league_position("1") == 1
    assert parse_league_position("T3") == 3
    assert parse_league_position("t12") == 12
    assert parse_league_position(None) is None
    assert parse_league_position("") is None
    assert parse_league_position("x") is None


def test_form_and_position_strength() -> None:
    assert form_strength([]) is None
    assert form_strength([W, W, W]) == 1.0
    assert form_strength([L, L, L]) == 0.0
    assert form_strength([W, D, L]) == 0.5
    assert position_strength(1, league_size=20) == 1.0
    assert position_strength(20, league_size=20) == 0.0
    assert position_strength(None, league_size=20) is None


def test_closeness_and_bands() -> None:
    assert closeness(0.8, 0.8) == 1.0
    assert closeness(1.0, 0.0) == 0.0
    assert band_for_score(0.0) == 1
    assert band_for_score(0.19) == 1
    assert band_for_score(0.2) == 2
    assert band_for_score(0.99) == 5
    assert band_for_score(1.0) == 5


def test_evenly_matched_is_harder_than_mismatch() -> None:
    even = calculate_match_difficulty(
        home_performance=[W, D, W, D, W],
        away_performance=[W, W, D, D, W],
        home_position="5",
        away_position="6",
        home_head_to_head=[W, D, L],
        away_head_to_head=[L, D, W],
        league_size=20,
    )
    mismatch = calculate_match_difficulty(
        home_performance=[W, W, W, W, W],
        away_performance=[L, L, L, L, L],
        home_position="1",
        away_position="20",
        home_head_to_head=[W, W, W],
        away_head_to_head=[L, L, L],
        league_size=20,
    )
    assert even.score > mismatch.score
    assert even.band >= mismatch.band
    assert 0.0 <= mismatch.score <= 1.0
    assert 0.0 <= even.score <= 1.0
    assert 1 <= even.band <= 5


def test_performance_weighs_more_than_h2h() -> None:
    """Closing the form gap should move score more than closing H2H alone."""
    base = calculate_match_difficulty(
        home_performance=[W, W, W, W, W],
        away_performance=[L, L, L, L, L],
        home_position="10",
        away_position="10",
        home_head_to_head=[W, W, W],
        away_head_to_head=[L, L, L],
        league_size=20,
    )
    closer_form = calculate_match_difficulty(
        home_performance=[W, W, D, D, D],
        away_performance=[D, D, D, L, L],
        home_position="10",
        away_position="10",
        home_head_to_head=[W, W, W],
        away_head_to_head=[L, L, L],
        league_size=20,
    )
    closer_h2h = calculate_match_difficulty(
        home_performance=[W, W, W, W, W],
        away_performance=[L, L, L, L, L],
        home_position="10",
        away_position="10",
        home_head_to_head=[W, D, L],
        away_head_to_head=[L, D, W],
        league_size=20,
    )
    assert closer_form.score - base.score > closer_h2h.score - base.score
    assert WEIGHT_PERFORMANCE > WEIGHT_LEAGUE_POSITION > WEIGHT_HEAD_TO_HEAD


def test_missing_signals_redistribute_and_neutral_fallback() -> None:
    only_form = calculate_match_difficulty(
        home_performance=[W, W],
        away_performance=[W, W],
        home_position=None,
        away_position=None,
        home_head_to_head=[],
        away_head_to_head=[],
    )
    assert only_form.score == 1.0
    assert only_form.band == 5

    empty = calculate_match_difficulty(
        home_performance=[],
        away_performance=[],
        home_position=None,
        away_position=None,
        home_head_to_head=[],
        away_head_to_head=[],
    )
    assert empty.score == 0.5
    assert empty.band == 3


def test_tied_table_positions_parse() -> None:
    difficulty = calculate_match_difficulty(
        home_performance=[D],
        away_performance=[D],
        home_position="T4",
        away_position="T4",
        home_head_to_head=[D],
        away_head_to_head=[D],
        league_size=24,
    )
    assert difficulty.score == 1.0
