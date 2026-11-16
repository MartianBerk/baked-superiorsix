"""SS-260: league table identity must not double on FC/AFC spelling splits."""

from datetime import UTC, date, datetime, timedelta

import anyio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.baked.superiorsix.tokens import auth_header, make_access_token

from baked.superiorsix.api.team_names import display_from_map, identity_display
from baked.superiorsix.domain.models import League, Match, MatchStatus
from baked.superiorsix.repositories.leagues import SqlAlchemyLeagueRepository
from baked.superiorsix.repositories.matches import SqlAlchemyMatchRepository
from baked.superiorsix.services.ingest import MatchIngestService
from baked.superiorsix.services.leagues import LeagueService, build_league_table, season_label
from baked.superiorsix.services.matches import MatchService
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


def test_display_matches_stats_key() -> None:
    """App labels use the same key as standings (xref then FC/AFC strip)."""
    assert identity_display("Arsenal FC") == "Arsenal"
    assert identity_display("Sunderland AFC") == "Sunderland"
    show = display_from_map({"Arsenal FC": "Arsenal FC", "Man Utd": "Manchester United"})
    assert show("Arsenal FC") == "Arsenal"
    assert show("Man Utd") == "Manchester United"
    assert show("Chelsea FC") == "Chelsea"


def test_livematches_shows_normalized_names_without_xref(client: TestClient) -> None:
    """List payloads remap home/away via stats_key even with an empty team_xref."""

    async def seed(session_factory: async_sessionmaker[AsyncSession]) -> tuple[int, date]:
        async with session_factory() as session:
            leagues = LeagueService(
                SqlAlchemyLeagueRepository(session),
                SqlAlchemyMatchRepository(session),
            )
            matches = MatchService(SqlAlchemyMatchRepository(session), leagues)
            ingest = MatchIngestService(matches, leagues)
            day = date.today() + timedelta(days=14)
            season_start_year = day.year if day.month >= 8 else day.year - 1
            league = await ingest.upsert_league(
                League(
                    name="Premier League",
                    code="PL",
                    start_date=date(season_start_year, 8, 1),
                )
            )
            assert league.id is not None
            fixture = await ingest.upsert_match(
                Match(
                    external_id="pl-fc-display",
                    league_id=league.id,
                    match_date=_kickoff(day),
                    status=MatchStatus.SCHEDULED,
                    home_team="Arsenal FC",
                    away_team="Chelsea FC",
                )
            )
            assert fixture.id is not None
            return fixture.id, day

    fixture_id, day = anyio.run(seed, client.app.state.session_factory)
    token, _ = make_access_token(roles=["admin"])
    headers = auth_header(token)
    opened = client.post(
        "/admin/addround",
        headers=headers,
        json={"start_date": date.today().isoformat(), "buy_in_pence": 100},
    )
    assert opened.status_code == 200, opened.text
    selected = client.post(
        "/admin/addmatch",
        headers=headers,
        json={"id": fixture_id, "game_number": 1},
    )
    assert selected.status_code == 200, selected.text
    # Admin list also remaps via load_display.
    assert selected.json()["home_team"] == "Arsenal"
    assert selected.json()["away_team"] == "Chelsea"

    live = client.get(
        "/game/livematches",
        params={"matchDate": day.strftime("%d-%m-%Y")},
    )
    assert live.status_code == 200, live.text
    row = live.json()["matches"][0]
    assert row["home_team"] == "Arsenal"
    assert row["away_team"] == "Chelsea"

    detail = client.get("/game/matchdetail", params={"id": fixture_id})
    assert detail.status_code == 200, detail.text
    body = detail.json()["match_detail"]
    # Provider strings stay on home_team; FE labels / map keys use *_xref.
    assert body["home_team"] == "Arsenal FC"
    assert body["away_team"] == "Chelsea FC"
    assert body["home_team_xref"] == "Arsenal"
    assert body["away_team_xref"] == "Chelsea"
    assert set(body["league_position"]) == {"Arsenal", "Chelsea"}
