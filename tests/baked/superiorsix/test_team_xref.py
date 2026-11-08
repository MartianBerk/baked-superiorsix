"""Team xref: API responses use display names; form/H2H merge across aliases."""

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
from baked.superiorsix.services.leagues import LeagueService
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


def test_api_responses_use_xref_display_names(client: TestClient) -> None:
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
    matches = listed.json()["matches"]
    row = next(item for item in matches if item["external_id"] == "pl-ars-che-xref")
    assert row["home_team"] == "Arsenal"
    assert row["away_team"] == "Chelsea"

    selected = client.post(
        "/admin/addmatch",
        headers=headers,
        json={"id": row["id"], "game_number": 1},
    )
    assert selected.status_code == 200, selected.text
    assert selected.json()["home_team"] == "Arsenal"
    assert selected.json()["away_team"] == "Chelsea"

    live = client.get("/game/livematches", params={"matchDate": _fmt(day)})
    assert live.status_code == 200, live.text
    assert live.json()["matches"][0]["home_team"] == "Arsenal"
    assert live.json()["matches"][0]["away_team"] == "Chelsea"

    # FE can call matchdetail with display names from livematches.
    detail = client.get(
        "/game/matchdetail",
        params={"hometeam": "Arsenal", "awayteam": "Chelsea", "matchdate": _fmt(day)},
    )
    assert detail.status_code == 200, detail.text
    body = detail.json()["match_detail"]
    assert set(body["league_position"]) == {"Arsenal", "Chelsea"}
    assert set(body["team_performance"]) == {"Arsenal", "Chelsea"}
    assert set(body["head_to_head"]) == {"Arsenal", "Chelsea"}
    # Form includes historical "Arsenal FC" results via xref aliases.
    assert body["team_performance"]["Arsenal"] == ["WIN", "DRAW", "WIN", "WIN"]
    assert body["head_to_head"]["Arsenal"] == ["WIN", "DRAW"]


def test_admin_upsert_team_xref(client: TestClient) -> None:
    headers = _admin_headers()
    created = client.post(
        "/admin/upsertteamxref",
        headers=headers,
        json={"team_name": "Manchester United FC", "xref": "Man Utd"},
    )
    assert created.status_code == 200, created.text
    assert created.json()["team_xref"][0] == {
        "team_name": "Manchester United FC",
        "xref": "Man Utd",
    }

    listed = client.get("/admin/listteamxref", headers=headers)
    assert listed.status_code == 200, listed.text
    assert any(
        row["team_name"] == "Manchester United FC" and row["xref"] == "Man Utd"
        for row in listed.json()["team_xref"]
    )
