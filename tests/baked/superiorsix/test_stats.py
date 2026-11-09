"""SS-18: previous-round success rates + full historic winners archive."""

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
                status=MatchStatus.SCHEDULED,
                home_team=home,
                away_team=away,
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


def test_stats_previous_round_rates_and_full_winners_archive(
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
    round1 = opened.json()["id"]

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

    # Default scope=previous is empty while the only round is still open.
    during = client.get("/stats/aggregate")
    assert during.status_code == 200, during.text
    assert during.json() == {"stats": []}

    # scope=current returns open-round rates before close (Performance graph).
    current = client.get("/stats/aggregate", params={"scope": "current"})
    assert current.status_code == 200, current.text
    current_by = {row["playerid"]: row for row in current.json()["stats"]}
    assert set(current_by) == {str(ada_id), str(bob_id)}
    assert current_by[str(ada_id)]["correct"] == 2
    assert current_by[str(ada_id)]["matches"] == 2
    assert current_by[str(ada_id)]["success_rate"] == 100.0
    assert len(current_by[str(ada_id)]["scores"]) == 2
    assert current_by[str(bob_id)]["success_rate"] == 0.0

    ended = client.post(
        "/admin/endround",
        headers=admin,
        params={"endDate": "20-01-2026"},
        json={"winner_ids": [str(ada_id), str(bob_id)]},
    )
    assert ended.status_code == 200, ended.text

    # After close: current is empty; previous (n−1) has the closed-round rates.
    assert client.get("/stats/aggregate", params={"scope": "current"}).json() == {
        "stats": []
    }
    aggregate = client.get("/stats/aggregate")
    assert aggregate.status_code == 200, aggregate.text
    by_player = {row["playerid"]: row for row in aggregate.json()["stats"]}
    assert set(by_player) == {str(ada_id), str(bob_id)}
    assert by_player[str(ada_id)]["correct"] == 2
    assert by_player[str(ada_id)]["matches"] == 2
    assert by_player[str(ada_id)]["success_rate"] == 100.0
    assert by_player[str(bob_id)]["success_rate"] == 0.0
    assert aggregate.json()["stats"][0]["playerid"] == str(ada_id)
    ada_scores = {point["date"]: point for point in by_player[str(ada_id)]["scores"]}
    assert ada_scores[day_a.isoformat()]["score"] == 1
    assert ada_scores[day_b.isoformat()]["score"] == 1

    filtered = client.get(
        "/stats/aggregate",
        params={"start_date": "17-01-2026", "end_date": "17-01-2026"},
    )
    assert filtered.status_code == 200, filtered.text
    filtered_ada = next(
        row for row in filtered.json()["stats"] if row["playerid"] == str(ada_id)
    )
    assert filtered_ada["correct"] == 1
    assert filtered_ada["matches"] == 1
    assert filtered_ada["success_rate"] == 100.0

    winners = client.get("/stats/winners")
    assert winners.status_code == 200, winners.text
    rounds = winners.json()["rounds"]
    assert len(rounds) == 1
    row = rounds[0]
    assert row["round_id"] == round1
    assert row["start_date"] == "2026-01-01"
    assert row["end_date"] == "2026-01-20"
    assert row["matches"] == 2
    assert row["players"] == 2
    assert row["jackpot"] == 400
    assert row["winner"] == "Ada Lovelace & Bob Builder"
    assert set(row["winner_ids"]) == {str(ada_id), str(bob_id)}

    historic = client.get("/admin/historicrounds", headers=admin)
    assert historic.status_code == 200, historic.text
    assert historic.json() == winners.json()

    # Second closed round — winners archive grows; aggregate stays on n−1 only.
    fixture_c = anyio.run(
        partial(
            _seed_fixture,
            session_factory,
            external_id="stats-c",
            home="Spurs",
            away="West Ham",
            kickoff=datetime(2026, 2, 7, 15, 0, tzinfo=UTC),
        )
    )
    assert fixture_c.id is not None
    opened2 = client.post(
        "/admin/addround",
        headers=admin,
        json={"start_date": "2026-02-01", "buy_in_pence": 100},
    )
    assert opened2.status_code == 200, opened2.text
    round2 = opened2.json()["id"]
    selected2 = client.post(
        "/admin/addmatch",
        headers=admin,
        json={"id": fixture_c.id, "game_number": 1},
    )
    assert selected2.status_code == 200, selected2.text
    assert (
        client.post(
            "/admin/addprediction",
            headers=admin,
            json={"auth_id": str(bob_id), "match_id": fixture_c.id, "prediction": "home"},
        ).status_code
        == 200
    )
    anyio.run(
        partial(
            _finish_match,
            session_factory,
            match_id=fixture_c.id,
            home_score=3,
            away_score=0,
        )
    )
    ended2 = client.post(
        "/admin/endround",
        headers=admin,
        params={"endDate": "10-02-2026"},
        json={"winner_ids": [str(bob_id)]},
    )
    assert ended2.status_code == 200, ended2.text

    archive = client.get("/stats/winners")
    assert archive.status_code == 200, archive.text
    archive_rounds = archive.json()["rounds"]
    assert len(archive_rounds) == 2
    assert {row["round_id"] for row in archive_rounds} == {round1, round2}
    # Newest start first.
    assert archive_rounds[0]["round_id"] == round2
    assert archive_rounds[0]["winner"] == "Bob Builder"
    assert archive_rounds[1]["round_id"] == round1

    # Aggregate previous is round2 only — not full score history.
    n1 = client.get("/stats/aggregate")
    assert n1.status_code == 200, n1.text
    n1_by = {row["playerid"]: row for row in n1.json()["stats"]}
    assert set(n1_by) == {str(bob_id)}
    assert n1_by[str(bob_id)]["correct"] == 1
    assert n1_by[str(bob_id)]["matches"] == 1
    assert n1_by[str(bob_id)]["success_rate"] == 100.0
    assert str(ada_id) not in n1_by
    assert client.get("/stats/aggregate", params={"scope": "current"}).json() == {
        "stats": []
    }


def test_aggregate_rejects_bad_scope(client: TestClient) -> None:
    response = client.get("/stats/aggregate", params={"scope": "all"})
    assert response.status_code == 422


def test_historicrounds_requires_admin(client: TestClient) -> None:
    response = client.get("/admin/historicrounds")
    assert response.status_code == 401

    player_token, _ = make_access_token(roles=["player"])
    forbidden = client.get("/admin/historicrounds", headers=auth_header(player_token))
    assert forbidden.status_code == 403
