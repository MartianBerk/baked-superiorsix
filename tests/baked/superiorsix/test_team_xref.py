"""Legacy TEAM_XREF: meta.teams + form / H2H / league-table alias merge."""

from datetime import UTC, date, datetime, timedelta

import anyio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.baked.superiorsix.tokens import auth_header, make_access_token

from baked.superiorsix.domain.models import League, Match, MatchStatus, TeamXref
from baked.superiorsix.repositories.leagues import SqlAlchemyLeagueRepository
from baked.superiorsix.repositories.matches import SqlAlchemyMatchRepository
from baked.superiorsix.repositories.team_xref import SqlAlchemyTeamXrefRepository
from baked.superiorsix.services.ingest import MatchIngestService
from baked.superiorsix.services.leagues import LeagueService, build_league_table, season_label
from baked.superiorsix.services.matches import MatchService
from baked.superiorsix.services.team_xref import TeamXrefService


def _kickoff(day: date, hour: int = 15) -> datetime:
    return datetime(day.year, day.month, day.day, hour, 0, tzinfo=UTC)


def _fmt(day: date) -> str:
    return day.strftime("%d-%m-%Y")


def _admin_headers() -> dict[str, str]:
    token, _ = make_access_token(roles=["admin"])
    return auth_header(token)


async def _seed(
    session_factory: async_sessionmaker[AsyncSession],
) -> date:
    async with session_factory() as session:
        team_xref = TeamXrefService(SqlAlchemyTeamXrefRepository(session))
        await team_xref.upsert_many(
            [
                TeamXref(team_name="Arsenal FC", xref="Arsenal"),
                TeamXref(team_name="Arsenal", xref="Arsenal"),
                TeamXref(team_name="Chelsea FC", xref="Chelsea"),
                TeamXref(team_name="Chelsea", xref="Chelsea"),
            ]
        )
        leagues = LeagueService(
            SqlAlchemyLeagueRepository(session),
            SqlAlchemyMatchRepository(session),
            team_xref,
        )
        matches = MatchService(SqlAlchemyMatchRepository(session), leagues, team_xref)
        ingest = MatchIngestService(matches, leagues)

        day = date.today() + timedelta(days=21)
        season_start_year = day.year if day.month >= 8 else day.year - 1
        league = await ingest.upsert_league(
            League(
                name="Premier League",
                code="PL",
                start_date=date(season_start_year, 8, 1),
            )
        )
        assert league.id is not None

        # Historical rows use a different provider spelling than the upcoming fixture.
        history = [
            ("hist-xref-1", "Arsenal FC", "Chelsea FC", 2, 1, day - timedelta(days=28)),
            ("hist-xref-2", "Chelsea FC", "Arsenal FC", 0, 0, day - timedelta(days=21)),
            ("hist-xref-3", "Arsenal FC", "Brighton", 3, 0, day - timedelta(days=14)),
            ("hist-xref-4", "Arsenal FC", "Everton", 1, 0, day - timedelta(days=7)),
        ]
        for external_id, home, away, home_score, away_score, when in history:
            await ingest.upsert_match(
                Match(
                    external_id=external_id,
                    league_id=league.id,
                    match_date=_kickoff(when),
                    status=MatchStatus.FINISHED,
                    home_team=home,
                    away_team=away,
                    home_score=home_score,
                    away_score=away_score,
                    match_minute=90,
                )
            )

        fixture = await ingest.upsert_match(
            Match(
                external_id="pl-ars-che-xref",
                league_id=league.id,
                match_date=_kickoff(day),
                status=MatchStatus.SCHEDULED,
                home_team="Arsenal",
                away_team="Chelsea",
            )
        )
        assert fixture.id is not None
        return day


def test_meta_teams_and_admin_upsert(client: TestClient) -> None:
    headers = _admin_headers()

    empty = client.get("/game/meta")
    assert empty.status_code == 200
    assert empty.json()["meta"]["teams"] == {}

    created = client.post(
        "/admin/upsertteamxref",
        headers=headers,
        json=[
            {"team_name": "Man Utd", "xref": "Manchester United"},
            {"team_name": "Manchester United", "xref": "Manchester United"},
        ],
    )
    assert created.status_code == 200, created.text
    assert {row["team_name"] for row in created.json()["team_xref"]} == {
        "Man Utd",
        "Manchester United",
    }

    listed = client.get("/admin/listteamxref", headers=headers)
    assert listed.status_code == 200
    assert listed.json()["team_xref"] == created.json()["team_xref"]

    meta = client.get("/game/meta")
    assert meta.status_code == 200
    assert meta.json()["meta"]["teams"] == {
        "Man Utd": "Manchester United",
        "Manchester United": "Manchester United",
    }
    assert client.get("/euros/meta").json()["meta"]["teams"]["Man Utd"] == "Manchester United"
    assert client.get("/worldcup/meta").json()["teams"]["Man Utd"] == "Manchester United"


def test_form_h2h_merge_via_xref_aliases(client: TestClient) -> None:
    day = anyio.run(_seed, client.app.state.session_factory)
    headers = _admin_headers()

    meta = client.get("/game/meta")
    assert meta.status_code == 200, meta.text
    teams = meta.json()["meta"]["teams"]
    assert teams["Arsenal FC"] == "Arsenal"
    assert teams["Chelsea FC"] == "Chelsea"

    opened = client.post(
        "/admin/addround",
        headers=headers,
        json={"start_date": date.today().isoformat(), "buy_in_pence": 100},
    )
    assert opened.status_code == 200, opened.text

    listed = client.get(
        "/admin/listmatches",
        headers=headers,
        params={"fromDate": _fmt(day), "toDate": _fmt(day)},
    )
    assert listed.status_code == 200, listed.text
    row = next(
        item for item in listed.json()["matches"] if item["external_id"] == "pl-ars-che-xref"
    )
    selected = client.post(
        "/admin/addmatch",
        headers=headers,
        json={"id": row["id"], "game_number": 1},
    )
    assert selected.status_code == 200, selected.text

    # Public livematches keep provider strings (FE labels via meta.teams).
    live = client.get("/game/livematches", params={"matchDate": _fmt(day)})
    assert live.status_code == 200, live.text
    assert live.json()["matches"][0]["home_team"] == "Arsenal"
    assert live.json()["matches"][0]["away_team"] == "Chelsea"

    detail = client.get(
        "/game/matchdetail",
        params={"hometeam": "Arsenal", "awayteam": "Chelsea", "matchdate": _fmt(day)},
    )
    assert detail.status_code == 200, detail.text
    body = detail.json()["match_detail"]
    # Response keys stay the requested provider strings.
    assert set(body["league_position"]) == {"Arsenal", "Chelsea"}
    assert set(body["team_performance"]) == {"Arsenal", "Chelsea"}
    assert set(body["head_to_head"]) == {"Arsenal", "Chelsea"}
    # Form / H2H include historical "Arsenal FC" / "Chelsea FC" via aliases.
    assert body["team_performance"]["Arsenal"] == ["WIN", "DRAW", "WIN", "WIN"]
    assert body["head_to_head"]["Arsenal"] == ["WIN", "DRAW"]
    assert body["league_position"]["Arsenal"] == "1"

    # Alias query finds the same fixture; keys stay the requested alias strings.
    aliased = client.get(
        "/game/matchdetail",
        params={"hometeam": "Arsenal FC", "awayteam": "Chelsea", "matchdate": _fmt(day)},
    )
    assert aliased.status_code == 200, aliased.text
    aliased_body = aliased.json()["match_detail"]
    assert set(aliased_body["team_performance"]) == {"Arsenal FC", "Chelsea"}
    assert aliased_body["team_performance"]["Arsenal FC"] == ["WIN", "DRAW", "WIN", "WIN"]
    assert aliased_body["league_position"]["Arsenal FC"] == "1"

    async def table_teams() -> list[str]:
        async with client.app.state.session_factory() as session:
            xref = TeamXrefService(SqlAlchemyTeamXrefRepository(session))
            leagues = LeagueService(
                SqlAlchemyLeagueRepository(session),
                SqlAlchemyMatchRepository(session),
                xref,
            )
            table = await leagues.league_table("PL")
            return [entry.team for entry in table]

    names = anyio.run(table_teams)
    assert "Arsenal" in names
    assert "Arsenal FC" not in names


def test_build_league_table_canonical_keys() -> None:
    day = date(2025, 9, 1)
    matches = [
        Match(
            external_id="a",
            league_id=1,
            match_date=_kickoff(day),
            status=MatchStatus.FINISHED,
            home_team="Arsenal FC",
            away_team="Chelsea",
            home_score=2,
            away_score=0,
        ),
        Match(
            external_id="b",
            league_id=1,
            match_date=_kickoff(day + timedelta(days=7)),
            status=MatchStatus.FINISHED,
            home_team="Arsenal",
            away_team="Spurs",
            home_score=1,
            away_score=0,
        ),
    ]
    table = build_league_table(
        "PL",
        season_label(date(2025, 8, 1)),
        matches,
        team_canonical={"Arsenal FC": "Arsenal", "Arsenal": "Arsenal"},
    )
    by_team = {entry.team: entry for entry in table}
    assert "Arsenal" in by_team
    assert "Arsenal FC" not in by_team
    assert by_team["Arsenal"].matches_played == 2
    assert by_team["Arsenal"].points == 6
