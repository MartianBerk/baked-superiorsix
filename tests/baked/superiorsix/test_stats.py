"""SS-18 stats aggregate and historic winners APIs."""

from datetime import UTC, date, datetime
from functools import partial
from uuid import UUID

import anyio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.baked.superiorsix.fakes import FakeUserProfileClient
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


async def _seed_fixture(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    external_id: str,
    home: str,
    away: str,
    kickoff: datetime,
    status: str = MatchStatus.SCHEDULED,
    home_score: int | None = None,
    away_score: int | None = None,
) -> Match:
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
                match_date=kickoff,
                status=status,
                home_team=home,
                away_team=away,
                home_score=home_score,
                away_score=away_score,
            )
        )
        assert fixture.id is not None
        return fixture


async def _finish_match(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    match_id: int,
    home_score: int,
    away_score: int,
) -> None:
    async with session_factory() as session:
        ingest = _ingest(session)
        await ingest.update_score(
            match_id=match_id,
            status=MatchStatus.FINISHED,
            home_score=home_score,
            away_score=away_score,
        )


def _admin_headers() -> dict[str, str]:
    token, _ = make_access_token(roles=["admin"])
    return auth_header(token)


def test_aggregate_and_winners_empty_without_data(client: TestClient) -> None:
    aggregate = client.get("/stats/aggregate")
    assert aggregate.status_code == 200, aggregate.text
    assert aggregate.json() == {"stats": []}

    winners = client.get("/stats/winners")
    assert winners.status_code == 200, winners.text
    assert winners.json() == {"rounds": []}


def test_aggregate_rejects_bad_date(client: TestClient) -> None:
    response = client.get("/stats/aggregate", params={"start_date": "2026-01-01"})
    assert response.status_code == 400
    assert "dd-mm-yyyy" in response.json()["detail"]


def test_stats_aggregate_and_historic_winners(
    client: TestClient,
    user_profiles: FakeUserProfileClient,
) -> None:
    session_factory = client.app.state.session_factory
    day_a = date(2026, 1, 10)
    day_b = date(2026, 1, 17)
    kickoff_a = datetime(2026, 1, 10, 15, 0, tzinfo=UTC)
    kickoff_b = datetime(2026, 1, 17, 15, 0, tzinfo=UTC)

    fixture_a = anyio.run(
        partial(
            _seed_fixture,
            session_factory,
            external_id="stats-a",
            home="Arsenal",
            away="Chelsea",
            kickoff=kickoff_a,
        )
    )
    fixture_b = anyio.run(
        partial(
            _seed_fixture,
            session_factory,
            external_id="stats-b",
            home="Liverpool",
            away="Everton",
            kickoff=kickoff_b,
        )
    )
    assert fixture_a.id is not None and fixture_b.id is not None

    admin = _admin_headers()
    opened = client.post(
        "/admin/addround",
        headers=admin,
        json={"start_date": "2026-01-01", "buy_in_pence": 100},
    )
    assert opened.status_code == 200, opened.text
    round_id = opened.json()["id"]

    for match_id, game_number in ((fixture_a.id, 1), (fixture_b.id, 2)):
        selected = client.post(
            "/admin/addmatch",
            headers=admin,
            json={"id": match_id, "game_number": game_number},
        )
        assert selected.status_code == 200, selected.text

    ada_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    bob_id = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    user_profiles.seed(ada_id, first_name="Ada", last_name="Lovelace")
    user_profiles.seed(bob_id, first_name="Bob", last_name="Builder")

    for auth_id, match_id, prediction in (
        (ada_id, fixture_a.id, "home"),
        (ada_id, fixture_b.id, "draw"),
        (bob_id, fixture_a.id, "away"),
        (bob_id, fixture_b.id, "home"),
    ):
        added = client.post(
            "/admin/addprediction",
            headers=admin,
            json={
                "auth_id": str(auth_id),
                "match_id": match_id,
                "prediction": prediction,
            },
        )
        assert added.status_code == 200, added.text

    anyio.run(
        partial(
            _finish_match,
            session_factory,
            match_id=fixture_a.id,
            home_score=2,
            away_score=1,
        )
    )
    anyio.run(
        partial(
            _finish_match,
            session_factory,
            match_id=fixture_b.id,
            home_score=1,
            away_score=1,
        )
    )

    aggregate = client.get("/stats/aggregate")
    assert aggregate.status_code == 200, aggregate.text
    by_player = {row["playerid"]: row for row in aggregate.json()["stats"]}
    assert set(by_player) == {str(ada_id), str(bob_id)}
    assert by_player[str(ada_id)]["name"] == "Ada Lovelace"
    ada_scores = {point["date"]: point for point in by_player[str(ada_id)]["scores"]}
    assert ada_scores[day_a.isoformat()] == {
        "name": "Ada Lovelace",
        "date": day_a.isoformat(),
        "score": 1,
        "matches": 1,
    }
    assert ada_scores[day_b.isoformat()] == {
        "name": "Ada Lovelace",
        "date": day_b.isoformat(),
        "score": 1,
        "matches": 1,
    }
    bob_scores = {point["date"]: point for point in by_player[str(bob_id)]["scores"]}
    assert bob_scores[day_a.isoformat()]["score"] == 0
    assert bob_scores[day_b.isoformat()]["score"] == 0

    filtered = client.get(
        "/stats/aggregate",
        params={"start_date": "17-01-2026", "end_date": "17-01-2026"},
    )
    assert filtered.status_code == 200, filtered.text
    filtered_by = {row["playerid"]: row for row in filtered.json()["stats"]}
    assert all(
        point["date"] == day_b.isoformat()
        for row in filtered_by.values()
        for point in row["scores"]
    )

    ended = client.post(
        "/admin/endround",
        headers=admin,
        params={"endDate": "20-01-2026"},
        json={"winner_ids": [str(ada_id), str(bob_id)]},
    )
    assert ended.status_code == 200, ended.text

    winners = client.get("/stats/winners")
    assert winners.status_code == 200, winners.text
    rounds = winners.json()["rounds"]
    assert len(rounds) == 1
    row = rounds[0]
    assert row["round_id"] == round_id
    assert row["start_date"] == "2026-01-01"
    assert row["end_date"] == "2026-01-20"
    assert row["matches"] == 2
    assert row["players"] == 2
    # buy_in 100 × 2 match-days × 2 predictors
    assert row["jackpot"] == 400
    assert row["winner"] == "Ada Lovelace & Bob Builder"
    assert set(row["winner_ids"]) == {str(ada_id), str(bob_id)}

    historic = client.get("/admin/historicrounds", headers=admin)
    assert historic.status_code == 200, historic.text
    assert historic.json() == winners.json()


def test_historicrounds_requires_admin(client: TestClient) -> None:
    response = client.get("/admin/historicrounds")
    assert response.status_code == 401

    player_token, _ = make_access_token(roles=["player"])
    forbidden = client.get("/admin/historicrounds", headers=auth_header(player_token))
    assert forbidden.status_code == 403
