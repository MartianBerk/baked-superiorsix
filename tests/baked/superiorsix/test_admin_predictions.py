from datetime import UTC, date, datetime, timedelta
from functools import partial
from uuid import UUID

import anyio
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.baked.superiorsix.fakes import FakeUserProfileClient
from tests.baked.superiorsix.test_predictions import (
    _FakeMatches,
    _FakePredictions,
    _FakeRounds,
    _fixture,
)
from tests.baked.superiorsix.tokens import auth_header, make_access_token

from baked.superiorsix.domain.exceptions import PredictionError
from baked.superiorsix.domain.models import League, Match, MatchStatus, Round
from baked.superiorsix.repositories.leagues import SqlAlchemyLeagueRepository
from baked.superiorsix.repositories.matches import SqlAlchemyMatchRepository
from baked.superiorsix.services.ingest import MatchIngestService
from baked.superiorsix.services.leagues import LeagueService
from baked.superiorsix.services.matches import MatchService
from baked.superiorsix.services.predictions import PredictionService


def _ingest(session: AsyncSession) -> MatchIngestService:
    leagues = LeagueService(SqlAlchemyLeagueRepository(session), SqlAlchemyMatchRepository(session))
    matches = MatchService(SqlAlchemyMatchRepository(session), leagues)
    return MatchIngestService(matches, leagues)


async def _seed_selected_fixture(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    external_id: str = "admin-pred-ars-che",
    kickoff: datetime | None = None,
) -> Match:
    kickoff_at = kickoff or datetime.now(UTC) + timedelta(days=7)
    async with session_factory() as session:
        ingest = _ingest(session)
        league = await ingest.upsert_league(
            League(name="Premier League", code="PL", start_date=date(2025, 8, 1))
        )
        assert league.id is not None
        fixture = await ingest.upsert_match(
            Match(
                external_id=external_id,
                league_id=league.id,
                match_date=kickoff_at,
                status=MatchStatus.SCHEDULED,
                home_team="Arsenal",
                away_team="Chelsea",
            )
        )
        assert fixture.id is not None
        return fixture


def _admin_headers() -> dict[str, str]:
    token, _ = make_access_token(roles=["admin"])
    return auth_header(token)


def test_admin_prediction_board_add_drop(
    client: TestClient,
    user_profiles: FakeUserProfileClient,
) -> None:
    fixture = anyio.run(_seed_selected_fixture, client.app.state.session_factory)
    assert fixture.id is not None
    admin = _admin_headers()

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
    subround_id = selected.json()["subround_id"]
    assert subround_id is not None

    _, auth_id = make_access_token(roles=["player"])
    user_profiles.seed(
        auth_id,
        first_name="Ada",
        last_name="Lovelace",
        email="ada@example.com",
        nickname="Ada",
    )

    board = client.get("/admin/listpredictionsnew", headers=admin)
    assert board.status_code == 200, board.text
    body = board.json()
    assert body["subround"]["id"] == subround_id
    assert body["match_count"] == 1
    assert len(body["matches"]) == 1
    assert body["predictions"] == []
    assert len(body["players"]) == 1
    player = body["players"][0]
    assert player["auth_id"] == str(auth_id)
    assert player["prediction_count"] == 0
    assert player["match_count"] == 1
    assert player["predictions"] == []

    created = client.post(
        "/admin/addprediction",
        headers=admin,
        json={"auth_id": str(auth_id), "match_id": fixture.id, "prediction": "H"},
    )
    assert created.status_code == 200, created.text
    assert created.json()["prediction"] == "home"
    prediction_id = created.json()["id"]

    board = client.get("/admin/listpredictionsnew", headers=admin)
    player = board.json()["players"][0]
    assert player["prediction_count"] == 1
    assert player["predictions"][0]["prediction"] == "home"
    assert player["predictions"][0]["match_id"] == fixture.id
    assert player["predictions"][0]["game_number"] == 1
    # SS-27 reads the flat predictions array (also via ?matchdate=).
    assert len(board.json()["predictions"]) == 1
    assert board.json()["predictions"][0]["auth_id"] == str(auth_id)
    assert board.json()["predictions"][0]["prediction"] == "home"

    match_day = fixture.match_date.astimezone(UTC).strftime("%d-%m-%Y")
    by_day = client.get(
        "/admin/listpredictionsnew",
        headers=admin,
        params={"matchdate": match_day},
    )
    assert by_day.status_code == 200, by_day.text
    assert len(by_day.json()["predictions"]) == 1
    assert by_day.json()["predictions"][0]["match_id"] == fixture.id

    flat = client.get(
        "/admin/listpredictions",
        headers=admin,
        params={"authId": str(auth_id)},
    )
    assert flat.status_code == 200
    assert len(flat.json()["predictions"]) == 1
    assert flat.json()["predictions"][0]["prediction"] == "home"

    updated = client.post(
        "/admin/addprediction",
        headers=admin,
        json={"auth_id": str(auth_id), "match_id": fixture.id, "prediction": "draw"},
    )
    assert updated.status_code == 200
    assert updated.json()["id"] == prediction_id
    assert updated.json()["prediction"] == "draw"

    dropped = client.get(
        "/admin/dropprediction",
        headers=admin,
        params={"id": prediction_id},
    )
    assert dropped.status_code == 200
    assert dropped.json()["dropped"] is True

    board = client.get("/admin/listpredictionsnew", headers=admin)
    player = board.json()["players"][0]
    assert player["prediction_count"] == 0
    assert player["predictions"] == []
    assert board.json()["predictions"] == []

    revived = client.post(
        "/admin/addprediction",
        headers=admin,
        json={"auth_id": str(auth_id), "match_id": fixture.id, "prediction": "A"},
    )
    assert revived.status_code == 200
    assert revived.json()["id"] == prediction_id
    assert revived.json()["prediction"] == "away"
    assert revived.json()["dropped"] is False


def test_admin_addprediction_past_cutoff(
    client: TestClient,
    user_profiles: FakeUserProfileClient,
) -> None:
    """Legacy admin addprediction did not enforce cutoff; keep that convenience."""
    kickoff = datetime.now(UTC) + timedelta(minutes=5)
    fixture = anyio.run(
        partial(
            _seed_selected_fixture,
            client.app.state.session_factory,
            external_id="admin-cutoff",
            kickoff=kickoff,
        )
    )
    assert fixture.id is not None
    admin = _admin_headers()

    assert (
        client.post(
            "/admin/addround",
            headers=admin,
            json={"start_date": date.today().isoformat(), "buy_in_pence": 50},
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/admin/addmatch",
            headers=admin,
            json={"id": fixture.id, "game_number": 1},
        ).status_code
        == 200
    )

    _, auth_id = make_access_token(roles=["player"])
    user_profiles.seed(auth_id, first_name="Past", last_name="Cutoff")

    response = client.post(
        "/admin/addprediction",
        headers=admin,
        json={"auth_id": str(auth_id), "match_id": fixture.id, "prediction": "home"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["prediction"] == "home"


def test_admin_prediction_requires_admin(client: TestClient) -> None:
    token, _ = make_access_token(roles=["player"])
    response = client.get(
        "/admin/listpredictionsnew",
        headers=auth_header(token),
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_submit_for_admin_skips_cutoff() -> None:
    alone = _fixture(
        match_id=1, kickoff=datetime(2026, 3, 14, 15, 0, tzinfo=UTC), game_number=1
    )
    service = PredictionService(
        _FakePredictions(),
        _FakeRounds(Round(id=1, start_date=date(2026, 1, 1), buy_in_pence=100)),
        _FakeMatches([alone]),
    )
    created = await service.submit_for_admin(
        auth_id=UUID("00000000-0000-0000-0000-000000000099"),
        match_id=alone.id,
        prediction="home",
        now=alone.match_date,
    )
    assert created.prediction == "home"

    with pytest.raises(PredictionError, match="Past cutoff"):
        await service.submit(
            auth_id=UUID("00000000-0000-0000-0000-000000000099"),
            match_id=alone.id,
            prediction="draw",
            now=alone.match_date,
        )
