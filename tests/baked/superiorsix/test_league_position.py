"""SS-260: league table identity must not double on FC/AFC spelling splits."""

from datetime import UTC, date, datetime, timedelta

from baked.superiorsix.domain.models import Match, MatchStatus
from baked.superiorsix.services.leagues import build_league_table, season_label
from baked.superiorsix.services.team_names import normalize_team_name, team_name_variants
from baked.superiorsix.services.team_xref import stats_key_for


def _kickoff(day: date, hour: int = 15) -> datetime:
    return datetime(day.year, day.month, day.day, hour, 0, tzinfo=UTC)


def test_normalize_team_name_strips_trailing_club_suffix() -> None:
    assert normalize_team_name("Arsenal FC") == "Arsenal"
    assert normalize_team_name("Sunderland AFC") == "Sunderland"
    assert normalize_team_name("AFC Bournemouth") == "AFC Bournemouth"
    assert normalize_team_name("  Chelsea  ") == "Chelsea"
    assert normalize_team_name("Nottingham Forest FC") == "Nottingham Forest"


def test_team_name_variants_cover_fc_spellings() -> None:
    variants = team_name_variants("Arsenal")
    assert "Arsenal" in variants
    assert "Arsenal FC" in variants
    assert "Arsenal AFC" in variants


def test_stats_key_for_merges_without_xref() -> None:
    assert stats_key_for("Arsenal FC", None) == "Arsenal"
    assert stats_key_for("Arsenal", None) == "Arsenal"
    assert stats_key_for("Arsenal FC", {"Arsenal FC": "Arsenal"}) == "Arsenal"
    assert stats_key_for("Arsenal FC", {"Arsenal": "The Gunners"}) == "The Gunners"


def test_build_league_table_merges_fc_split_without_xref() -> None:
    """PL-style doubling: same clubs under short + football-data long names."""
    day = date(2025, 9, 1)
    season = season_label(date(2025, 8, 1))
    matches = [
        Match(
            external_id="legacy-1",
            league_id=1,
            match_date=_kickoff(day),
            status=MatchStatus.FINISHED,
            home_team="Arsenal",
            away_team="Chelsea",
            home_score=2,
            away_score=0,
        ),
        Match(
            external_id="fd-1",
            league_id=1,
            match_date=_kickoff(day + timedelta(days=7)),
            status=MatchStatus.FINISHED,
            home_team="Chelsea FC",
            away_team="Arsenal FC",
            home_score=1,
            away_score=1,
        ),
        Match(
            external_id="fd-2",
            league_id=1,
            match_date=_kickoff(day + timedelta(days=14)),
            status=MatchStatus.FINISHED,
            home_team="Liverpool FC",
            away_team="Arsenal",
            home_score=0,
            away_score=3,
        ),
        Match(
            external_id="fd-3",
            league_id=1,
            match_date=_kickoff(day + timedelta(days=21)),
            status=MatchStatus.FINISHED,
            home_team="Sunderland AFC",
            away_team="Liverpool",
            home_score=1,
            away_score=0,
        ),
    ]
    table = build_league_table("PL", season, matches, team_canonical=None)
    by_team = {entry.team: entry for entry in table}

    assert set(by_team) == {"Arsenal", "Chelsea", "Liverpool", "Sunderland"}
    assert "Arsenal FC" not in by_team
    assert "Chelsea FC" not in by_team
    assert "Sunderland AFC" not in by_team
    assert by_team["Arsenal"].matches_played == 3
    assert by_team["Arsenal"].points == 7
    assert by_team["Arsenal"].position == "1"
    # Four clubs only — positions must not climb into the 5–8 range.
    assert max(int(entry.position.lstrip("T")) for entry in table) <= 4


def test_build_league_table_elc_lookup_keys_align() -> None:
    """ELC NULL case: history under short name, fixture under FC long name."""
    day = date(2025, 9, 1)
    season = season_label(date(2025, 8, 1))
    matches = [
        Match(
            external_id="elc-1",
            league_id=2,
            match_date=_kickoff(day),
            status=MatchStatus.FINISHED,
            home_team="Sheffield Wednesday",
            away_team="Watford",
            home_score=2,
            away_score=1,
        ),
        Match(
            external_id="elc-2",
            league_id=2,
            match_date=_kickoff(day + timedelta(days=7)),
            status=MatchStatus.FINISHED,
            home_team="Watford FC",
            away_team="Leeds United FC",
            home_score=0,
            away_score=0,
        ),
    ]
    table = build_league_table("ELC", season, matches, team_canonical=None)
    positions = {entry.team: entry.position for entry in table}

    assert stats_key_for("Sheffield Wednesday FC", None) in positions
    assert stats_key_for("Watford", None) in positions
    assert stats_key_for("Leeds United", None) in positions
    assert positions[stats_key_for("Sheffield Wednesday FC", None)] == "1"
