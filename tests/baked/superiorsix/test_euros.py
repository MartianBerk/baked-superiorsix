"""Euro Wizard (SS-19): scoring, APIs, isolated schema."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import anyio
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.baked.superiorsix.fakes import FakeAuthAdminClient, FakeUserProfileClient
from tests.baked.superiorsix.tokens import auth_header, make_access_token

from baked.superiorsix.domain.euros_scoring import score_prediction
from baked.superiorsix.domain.models import EurosMatch, EurosPrediction, League, MatchStatus
from baked.superiorsix.process.extractors.connectors.football_data import (
    parse_football_data_match,
)
from baked.superiorsix.repositories.euros import (
    SqlAlchemyEurosMatchRepository,
    SqlAlchemyEurosPredictionRepository,
    SqlAlchemyEurosTournamentRepository,
)
from baked.superiorsix.repositories.leagues import SqlAlchemyLeagueRepository
from baked.superiorsix.repositories.matches import SqlAlchemyMatchRepository
from baked.superiorsix.services.euros import EurosService
from baked.superiorsix.services.leagues import LeagueService


def _finished(
    *,
    matchday: int,
    home: int,
    away: int,
    et: bool = False,
    pens: bool = False,
) -> EurosMatch:
    return EurosMatch(
        id=1,
        external_id="fd-1",
        league_id=1,
        matchday=matchday,
        match_date=datetime(2024, 7, 1, 19, 0, tzinfo=UTC),
        status=MatchStatus.FINISHED,
        home_team="Home",
        away_team="Away",
        home_score=home,
        away_score=away,
        extra_time=et,
        penalties=pens,
    )


def test_group_stage_no_bonus() -> None:
    match = _finished(matchday=2, home=1, away=0)
    pred = EurosPrediction(
        auth_id=uuid4(),
        match_id=1,
        prediction="home",
        plus_ninety=False,
    )
    score, bonus = score_prediction(pred, match)
    assert score == 1
    assert bonus == 0


def test_knockout_ninety_bonus() -> None:
    match = _finished(matchday=4, home=2, away=1)
    pred = EurosPrediction(
        auth_id=uuid4(),
        match_id=1,
        prediction="home",
        plus_ninety=False,
    )
    score, bonus = score_prediction(pred, match)
    assert (score, bonus) == (1, 1)


def test_knockout_et_bonus() -> None:
    match = _finished(matchday=5, home=2, away=1, et=True)
    pred = EurosPrediction(
        auth_id=uuid4(),
        match_id=1,
        prediction="home",
        plus_ninety=True,
        extra_time=True,
    )
    assert score_prediction(pred, match) == (1, 2)


def test_knockout_pens_bonus() -> None:
    match = _finished(matchday=7, home=1, away=1, et=True, pens=True)
    # Base score is 0 for home when scores are level — legacy gates bonus on score > 0.
    pred_home = EurosPrediction(
        auth_id=uuid4(),
        match_id=1,
        prediction="home",
        plus_ninety=True,
        penalties=True,
    )
    assert score_prediction(pred_home, match) == (0, 0)

    pred_draw = EurosPrediction(
        auth_id=uuid4(),
        match_id=1,
        prediction="draw",
        plus_ninety=True,
        penalties=True,
    )
    # Draw + pens is rejected at submit time; scoring still follows the view math.
    assert score_prediction(pred_draw, match) == (1, 3)


def test_wrong_pick_zeroes_bonus() -> None:
    match = _finished(matchday=4, home=0, away=1, et=True)
    pred = EurosPrediction(
        auth_id=uuid4(),
        match_id=1,
        prediction="home",
        plus_ninety=True,
        extra_time=True,
    )
    assert score_prediction(pred, match) == (0, 0)


def test_parse_football_data_match_et_pens() -> None:
    raw = {
        "id": 99,
        "utcDate": "2024-07-14T19:00:00Z",
        "status": "FINISHED",
        "matchday": 7,
        "homeTeam": {"name": "Spain"},
        "awayTeam": {"name": "England"},
        "score": {
            "fullTime": {"home": 2, "away": 1},
            "extraTime": {"home": 1, "away": 0},
            "penalties": {"home": None, "away": None},
        },
    }
    payload = parse_football_data_match(raw)
    assert payload is not None
    assert payload.matchday == 7
    assert payload.extra_time is True
    assert payload.penalties is False
    assert payload.external_id == "fd-99"


async def _seed_euros_match(
    session_factory: async_sessionmaker[AsyncSession],
) -> EurosMatch:
    async with session_factory() as session:
        leagues = LeagueService(
            SqlAlchemyLeagueRepository(session),
            SqlAlchemyMatchRepository(session),
        )
        league = await leagues.upsert(
            League(name="European Championship", code="EC", start_date=date(2024, 6, 14))
        )
        assert league.id is not None
        euros = EurosService(
            SqlAlchemyEurosTournamentRepository(session),
            SqlAlchemyEurosMatchRepository(session),
            SqlAlchemyEurosPredictionRepository(session),
        )
        match = await euros.upsert_fixture(
            EurosMatch(
                external_id="fd-euros-e2e",
                league_id=league.id,
                matchday=1,
                match_date=datetime.now(UTC) + timedelta(days=3),
                status=MatchStatus.SCHEDULED,
                home_team="France",
                away_team="Germany",
            )
        )
        assert match.id is not None
        return match


def _seed_euros_player(
    profiles: FakeUserProfileClient,
    auth_admin: FakeAuthAdminClient,
):
    token, auth_id = make_access_token(roles=["euros", "player", "user-profile"])
    profiles.seed(
        auth_id,
        first_name="Euro",
        last_name="Wizard",
        email="euro@example.com",
    )
    auth_admin.memberships["euro@example.com"] = ["euros", "player", "user-profile"]
    return auth_id, token


def test_euros_public_routes_not_noop(client: TestClient) -> None:
    for path in (
        "/euros/matches",
        "/euros/scores",
        "/euros/listpredictions",
        "/eurowizard/listpredictions",
        "/euros/getpredictions",
        "/euros/meta",
    ):
        response = client.get(path)
        assert response.status_code == 200, path
        body = response.json()
        assert "noop" not in body, path


def test_euros_add_prediction_requires_euros_role(
    client: TestClient,
    user_profiles: FakeUserProfileClient,
    auth_admin: FakeAuthAdminClient,
) -> None:
    _seed_euros_player(user_profiles, auth_admin)
    admin_token, _ = make_access_token(roles=["admin"])
    open_resp = client.post(
        "/admin/euros/opentournament",
        headers=auth_header(admin_token),
        json={"start_date": "2024-06-01", "buy_in_pence": 500},
    )
    assert open_resp.status_code == 200
    assert open_resp.json()["entrant_count"] == 1
    assert open_resp.json()["prize_pot_pence"] == 500

    player_token, _ = make_access_token(roles=["player"])
    denied = client.post(
        "/euros/addprediction",
        headers=auth_header(player_token),
        json={"game_id": 1, "prediction": "home"},
    )
    assert denied.status_code == 403


def test_euros_prediction_flow(
    client: TestClient,
    user_profiles: FakeUserProfileClient,
    auth_admin: FakeAuthAdminClient,
) -> None:
    auth_id, token = _seed_euros_player(user_profiles, auth_admin)
    admin_token, _ = make_access_token(roles=["admin"])
    assert (
        client.post(
            "/admin/euros/opentournament",
            headers=auth_header(admin_token),
            json={"start_date": "2024-06-01", "buy_in_pence": 200},
        ).status_code
        == 200
    )

    match = anyio.run(_seed_euros_match, client.app.state.session_factory)
    assert match.id is not None

    created = client.post(
        "/euros/addprediction",
        headers=auth_header(token),
        json={
            "game_id": match.id,
            "prediction": "home",
            "extra_time": True,
            "penalties": False,
        },
    )
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["prediction"] == "home"
    assert body["plus_ninety"] is True
    assert body["extra_time"] is True
    assert body["penalties"] is False
    assert body["auth_id"] == str(auth_id)

    listed = client.get("/euros/listpredictions")
    assert listed.status_code == 200
    assert len(listed.json()["predictions"]) == 1

    got = client.get(
        "/euros/getprediction",
        headers=auth_header(token),
        params={"gameId": match.id},
    )
    assert got.status_code == 200
    assert got.json()["prediction"]["prediction"] == "home"

    bad = client.post(
        "/euros/addprediction",
        headers=auth_header(token),
        json={"game_id": match.id, "prediction": "draw", "extra_time": True},
    )
    assert bad.status_code == 400


def test_euros_tables_isolated_from_super_six(client: TestClient) -> None:
    assert client.get("/euros/matches").json() == {"matches": []}
    response = client.get("/game/livematches", params={"matchDate": "14-06-2024"})
    assert response.status_code == 200
    assert response.json() == {"matches": []}


def test_tournament_ec_requires_euros_service() -> None:
    from baked.superiorsix.process.extractors.tournament import TournamentExtractor

    extractor = TournamentExtractor(
        ingest=None,  # type: ignore[arg-type]
        leagues=None,  # type: ignore[arg-type]
        football_data_token=None,
        euros=None,
    )

    async def _call() -> None:
        await extractor.process_matches(
            league_code="EC",
            date_from=date(2024, 6, 14),
            date_to=date(2024, 6, 14),
        )

    with pytest.raises(ValueError, match="EurosService"):
        anyio.run(_call)
