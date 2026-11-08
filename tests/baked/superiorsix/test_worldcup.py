"""SS-20 World Cup side-game: scoring, APIs, static pot, ET/pens parse."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from tests.baked.superiorsix.fakes import FakeAuthAdminClient, FakeUserProfileClient
from tests.baked.superiorsix.tokens import auth_header, make_access_token

from baked.superiorsix.domain.worldcup import (
    WorldcupMatch,
    WorldcupPrediction,
    knockout_bonus_points,
    score_prediction_against_match,
    static_pot_pence,
)
from baked.superiorsix.process.extractors.connectors.football_data import (
    fold_side_game_full_time_scores,
    parse_football_data_side_game_match,
)


def test_static_pot_no_rollover() -> None:
    assert static_pot_pence(buy_in_pence=200, participant_count=14) == 2800
    # Unlike Super Six, pot does not multiply by round/subround.
    assert static_pot_pence(buy_in_pence=200, participant_count=14) == 14 * 200


@pytest.mark.parametrize(
    ("matchday", "plus_ninety", "pred_et", "pred_pens", "match_et", "match_pens", "expected"),
    [
        (1, False, False, False, False, False, 0),  # group stage
        (4, True, False, True, False, True, 3),  # pens
        (5, True, True, False, True, False, 2),  # ET
        (4, False, False, False, False, False, 1),  # finished in 90
        (4, True, True, False, False, True, 0),  # wrong mode
    ],
)
def test_knockout_bonus_matrix(
    matchday: int,
    plus_ninety: bool,
    pred_et: bool,
    pred_pens: bool,
    match_et: bool,
    match_pens: bool,
    expected: int,
) -> None:
    assert (
        knockout_bonus_points(
            matchday=matchday,
            plus_ninety=plus_ninety,
            predicted_extra_time=pred_et,
            predicted_penalties=pred_pens,
            match_extra_time=match_et,
            match_penalties=match_pens,
        )
        == expected
    )


def test_bonus_zero_when_result_wrong() -> None:
    match = WorldcupMatch(
        id=1,
        external_id="fd-1",
        league_id=1,
        matchday=4,
        match_date=datetime(2026, 7, 1, tzinfo=UTC),
        status="FINISHED",
        home_team="A",
        away_team="B",
        home_score=2,
        away_score=1,
        extra_time=False,
        penalties=False,
    )
    prediction = WorldcupPrediction(
        auth_id=UUID("00000000-0000-0000-0000-000000000001"),
        match_id=1,
        prediction="away",
        plus_ninety=False,
    )
    score, bonus = score_prediction_against_match(prediction, match)
    assert score == 0
    assert bonus == 0


def test_fold_penalty_shootout_scores() -> None:
    # fullTime 4-3 with pens 3-2 → fold to 2-1 (pre-pens 1-1 + winner +1).
    home, away = fold_side_game_full_time_scores(
        duration="PENALTY_SHOOTOUT",
        full_time_home=4,
        full_time_away=3,
        penalties_home=3,
        penalties_away=2,
    )
    assert (home, away) == (2, 1)


def test_parse_side_game_match_et_flags() -> None:
    raw = {
        "id": 99,
        "utcDate": "2026-07-05T19:00:00Z",
        "status": "FINISHED",
        "matchday": 5,
        "homeTeam": {"name": "Home"},
        "awayTeam": {"name": "Away"},
        "score": {
            "duration": "EXTRA_TIME",
            "fullTime": {"home": 2, "away": 1},
            "penalties": {"home": None, "away": None},
        },
    }
    payload = parse_football_data_side_game_match(raw)
    assert payload is not None
    assert payload.extra_time is True
    assert payload.penalties is False
    assert payload.matchday == 5
    assert payload.home_score == 2


async def _seed_league_and_match(client: TestClient) -> int:
    from sqlalchemy import select

    from baked.superiorsix.db.models import LeagueRecord, WorldcupMatchRecord

    async with client.app.state.session_factory() as session:
        session.add(
            LeagueRecord(name="FIFA World Cup", code="WC", start_date=datetime.now(UTC).date())
        )
        await session.commit()
        league = (await session.execute(select(LeagueRecord))).scalar_one()
        kickoff = datetime.now(UTC) + timedelta(hours=2)
        match = WorldcupMatchRecord(
            external_id="fd-wc-1",
            league_id=league.id,
            matchday=4,
            match_date=kickoff,
            status="SCHEDULED",
            home_team="Brazil",
            away_team="Argentina",
            use_match=True,
        )
        session.add(match)
        await session.commit()
        await session.refresh(match)
        return match.id


@pytest.mark.asyncio
async def test_worldcup_public_routes_and_prediction_flow(client: TestClient) -> None:
    match_id = await _seed_league_and_match(client)

    assert client.get("/worldcup/matches").json()["matches"][0]["home_team"] == "Brazil"
    assert client.get("/worldcup/scores").json() == {"scores": []}
    assert client.get("/worldcup/listpredictions").json() == {"predictions": []}
    assert client.get("/qatarhero/listpredictions").json() == {"predictions": []}
    meta0 = client.get("/worldcup/meta").json()
    assert meta0["pot_pence"] == 0
    assert meta0["open"] is False

    token, auth_id = make_access_token(roles=["worldcup"])
    headers = auth_header(token)

    empty = client.get(
        "/worldcup/getprediction",
        params={"gameId": match_id},
        headers=headers,
    )
    assert empty.status_code == 200
    assert empty.json() == {"prediction": None}

    bad = client.post(
        "/worldcup/addprediction",
        json={
            "game_id": match_id,
            "prediction": "draw",
            "extra_time": True,
        },
        headers=headers,
    )
    assert bad.status_code == 400

    created = client.post(
        "/worldcup/addprediction",
        json={
            "game_id": match_id,
            "prediction": "home",
            "extra_time": True,
            "penalties": False,
        },
        headers=headers,
    )
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["prediction"] == "home"
    assert body["plus_ninety"] is True
    assert body["extra_time"] is True
    assert body["auth_id"] == str(auth_id)

    listed = client.get("/worldcup/listpredictions").json()["predictions"]
    assert len(listed) == 1


@pytest.mark.asyncio
async def test_admin_worldcup_settings_static_pot(client: TestClient) -> None:
    token, _ = make_access_token(roles=["admin"])
    headers = auth_header(token)

    updated = client.post(
        "/admin/worldcup/settings",
        json={"buy_in_pence": 200, "participant_count": 14},
        headers=headers,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json() == {
        "buy_in_pence": 200,
        "participant_count": 14,
        "pot_pence": 2800,
        "open": False,
        "winner_ids": [],
    }
    assert client.get("/worldcup/meta").json()["pot_pence"] == 2800


@pytest.mark.asyncio
async def test_worldcup_open_end_tournament_lifecycle(
    client: TestClient,
    user_profiles: FakeUserProfileClient,
    auth_admin: FakeAuthAdminClient,
) -> None:
    _, auth_id = make_access_token(roles=["player", "user-profile"])
    user_profiles.seed(
        auth_id,
        first_name="World",
        last_name="Cup",
        email="wc@example.com",
    )
    auth_admin.memberships["wc@example.com"] = ["player", "user-profile"]

    admin_token, _ = make_access_token(roles=["admin"])
    headers = auth_header(admin_token)

    assert client.get("/worldcup/meta").json()["open"] is False

    empty = client.post(
        "/admin/worldcup/opentournament",
        headers=headers,
        json={"buy_in_pence": 200, "entrant_ids": []},
    )
    assert empty.status_code == 422

    opened = client.post(
        "/admin/worldcup/opentournament",
        headers=headers,
        json={"buy_in_pence": 200, "entrant_ids": [str(auth_id)]},
    )
    assert opened.status_code == 200, opened.text
    body = opened.json()
    assert body == {
        "buy_in_pence": 200,
        "participant_count": 1,
        "pot_pence": 200,
        "open": True,
        "winner_ids": [],
    }
    assert "worldcup" in auth_admin.memberships["wc@example.com"]
    assert client.get("/worldcup/meta").json()["open"] is True

    again = client.post(
        "/admin/worldcup/opentournament",
        headers=headers,
        json={"buy_in_pence": 100, "entrant_ids": [str(auth_id)]},
    )
    assert again.status_code == 400

    ended = client.post(
        "/admin/worldcup/endtournament",
        headers=headers,
        json={"winner_ids": [str(auth_id)]},
    )
    assert ended.status_code == 200, ended.text
    closed = ended.json()
    assert closed["open"] is False
    assert closed["participant_count"] == 1
    assert closed["pot_pence"] == 200
    assert closed["winner_ids"] == [str(auth_id)]
    assert client.get("/worldcup/meta").json()["open"] is False
    assert client.get("/worldcup/meta").json()["winner_ids"] == [str(auth_id)]

    # Re-open after end is allowed (clears prior winners until next close).
    reopened = client.post(
        "/admin/worldcup/opentournament",
        headers=headers,
        json={"buy_in_pence": 300, "entrant_ids": [str(auth_id)]},
    )
    assert reopened.status_code == 200
    assert reopened.json()["open"] is True
    assert reopened.json()["pot_pence"] == 300
    assert reopened.json()["winner_ids"] == []


@pytest.mark.asyncio
async def test_worldcup_scores_after_finished(client: TestClient) -> None:
    match_id = await _seed_league_and_match(client)
    token, auth_id = make_access_token(roles=["worldcup"])
    headers = auth_header(token)
    assert (
        client.post(
            "/worldcup/addprediction",
            json={"game_id": match_id, "prediction": "home"},
            headers=headers,
        ).status_code
        == 200
    )

    async with client.app.state.session_factory() as session:
        from baked.superiorsix.db.models import WorldcupMatchRecord

        match = await session.get(WorldcupMatchRecord, match_id)
        assert match is not None
        match.status = "FINISHED"
        match.home_score = 1
        match.away_score = 0
        match.extra_time = False
        match.penalties = False
        await session.commit()

    scores = client.get("/worldcup/scores").json()["scores"]
    assert len(scores) == 1
    assert scores[0]["auth_id"] == str(auth_id)
    assert scores[0]["score"] == 1
    assert scores[0]["bonus"] == 1  # knockout matchday, finished in 90
    assert scores[0]["total"] == 2
