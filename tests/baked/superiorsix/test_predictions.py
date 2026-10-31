from datetime import UTC, date, datetime, timedelta

import anyio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.baked.superiorsix.tokens import auth_header, make_access_token

from baked.superiorsix.domain.models import League, Match, MatchStatus
from baked.superiorsix.repositories.leagues import SqlAlchemyLeagueRepository
from baked.superiorsix.repositories.matches import SqlAlchemyMatchRepository
from baked.superiorsix.services.ingest import MatchIngestService
from baked.superiorsix.services.leagues import LeagueService
from baked.superiorsix.services.matches import MatchService


def _ingest(session: AsyncSession) -> MatchIngestService:
    leagues = LeagueService(SqlAlchemyLeagueRepository(session), SqlAlchemyMatchRepository(session))
    matches = MatchService(SqlAlchemyMatchRepository(session), leagues)
    return MatchIngestService(matches, leagues)


async def _seed_selected_fixture(
    session_factory: async_sessionmaker[AsyncSession],
) -> Match:
    kickoff_day = date.today() + timedelta(days=7)
    async with session_factory() as session:
        ingest = _ingest(session)
        league = await ingest.upsert_league(
            League(name="Premier League", code="PL", start_date=date(2025, 8, 1))
        )
        assert league.id is not None
        fixture = await ingest.upsert_match(
            Match(
                external_id="e2e-ars-che",
                league_id=league.id,
                matchday=1,
                match_date=datetime(
                    kickoff_day.year, kickoff_day.month, kickoff_day.day, 15, 0, tzinfo=UTC
                ),
                status=MatchStatus.SCHEDULED,
                home_team="Arsenal",
                away_team="Chelsea",
            )
        )
        assert fixture.id is not None
        return fixture


def test_user_prediction_end_to_end(client: TestClient) -> None:
    fixture = anyio.run(_seed_selected_fixture, client.app.state.session_factory)
    assert fixture.id is not None

    admin_token, _ = make_access_token(roles=["ADMIN"])
    admin = auth_header(admin_token)

    opened = client.post(
        "/admin/addround",
        headers=admin,
        json={"start_date": date.today().isoformat(), "buy_in_pence": 100},
    )
    assert opened.status_code == 200, opened.text

    selected = client.post(
        "/admin/addmatch",
        headers=admin,
        json={"id": fixture.id, "game_number": 1},
    )
    assert selected.status_code == 200, selected.text

    available = client.get(
        "/game/livematches",
        params={"matchDate": (date.today() + timedelta(days=7)).strftime("%d-%m-%Y")},
    )
    assert available.status_code == 200
    assert len(available.json()["matches"]) == 1

    player_token, auth_id = make_access_token(roles=["PLAYER"])
    player = auth_header(player_token)

    missing = client.get("/user/getprediction", headers=player, params={"gameId": fixture.id})
    assert missing.status_code == 200
    assert missing.json() == {"prediction": None}

    created = client.post(
        "/user/addprediction",
        headers=player,
        json={"game_id": fixture.id, "prediction": "home"},
    )
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["match_id"] == fixture.id
    assert body["prediction"] == "home"
    assert body["auth_id"] == str(auth_id)
    assert body["dropped"] is False

    fetched = client.get("/user/getprediction", headers=player, params={"gameId": fixture.id})
    assert fetched.status_code == 200
    assert fetched.json() == {"prediction": "home"}

    updated = client.post(
        "/user/addprediction",
        headers=player,
        json={"game_id": fixture.id, "prediction": "draw"},
    )
    assert updated.status_code == 200
    assert updated.json()["prediction"] == "draw"
    assert updated.json()["id"] == body["id"]

    bad = client.post(
        "/user/addprediction",
        headers=player,
        json={"game_id": fixture.id, "prediction": "win"},
    )
    assert bad.status_code == 400


def test_prediction_requires_player_role(client: TestClient) -> None:
    response = client.post(
        "/user/addprediction",
        json={"game_id": 1, "prediction": "home"},
    )
    assert response.status_code == 401
