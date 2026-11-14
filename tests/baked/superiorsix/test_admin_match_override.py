"""Admin match score override (SS-254): only from 2h after kickoff."""

from datetime import UTC, date, datetime, timedelta
from functools import partial

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


async def _seed_fixture(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    external_id: str,
    kickoff: datetime,
    status: str = MatchStatus.SCHEDULED,
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
                home_team="Arsenal",
                away_team="Chelsea",
            )
        )
        await session.commit()
        assert fixture.id is not None
        return fixture


def _seed(
    client: TestClient,
    *,
    external_id: str,
    kickoff: datetime,
    status: str = MatchStatus.SCHEDULED,
) -> Match:
    return anyio.run(
        partial(
            _seed_fixture,
            client.app.state.session_factory,
            external_id=external_id,
            kickoff=kickoff,
            status=status,
        )
    )


def _admin_headers() -> dict[str, str]:
    token, _ = make_access_token(roles=["admin"])
    return auth_header(token)


def test_admin_override_after_two_hours_sets_finished_and_scores(client: TestClient) -> None:
    fixture = _seed(
        client,
        external_id="override-past-3h",
        kickoff=datetime.now(UTC) - timedelta(hours=3),
    )
    assert fixture.id is not None
    admin = _admin_headers()

    response = client.post(
        f"/admin/matches/{fixture.id}/result",
        headers=admin,
        json={"home_score": 2, "away_score": 1},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == MatchStatus.FINISHED
    assert body["home_score"] == 2
    assert body["away_score"] == 1


def test_admin_override_rejects_before_two_hours_including_same_day(client: TestClient) -> None:
    # Same-day example: 15:00 kickoff viewed at ~12:00 (3h early) — blocked.
    fixture = _seed(
        client,
        external_id="override-same-day-early",
        kickoff=datetime.now(UTC) + timedelta(hours=3),
    )
    assert fixture.id is not None
    admin = _admin_headers()

    response = client.post(
        f"/admin/matches/{fixture.id}/result",
        headers=admin,
        json={"home_score": 3, "away_score": 0},
    )
    assert response.status_code == 400, response.text
    assert "2 hours after kickoff" in response.json()["detail"]


def test_admin_override_rejects_within_two_hours_after_kickoff(client: TestClient) -> None:
    fixture = _seed(
        client,
        external_id="override-within-1h",
        kickoff=datetime.now(UTC) - timedelta(hours=1),
        status=MatchStatus.IN_PLAY,
    )
    assert fixture.id is not None
    admin = _admin_headers()

    response = client.post(
        f"/admin/matches/{fixture.id}/result",
        headers=admin,
        json={"home_score": 0, "away_score": 0},
    )
    assert response.status_code == 400, response.text
    assert "2 hours after kickoff" in response.json()["detail"]


def test_admin_override_rejects_non_admin(client: TestClient) -> None:
    fixture = _seed(
        client,
        external_id="override-player",
        kickoff=datetime.now(UTC) - timedelta(hours=5),
    )
    assert fixture.id is not None
    token, _ = make_access_token(roles=["player"])

    response = client.post(
        f"/admin/matches/{fixture.id}/result",
        headers=auth_header(token),
        json={"home_score": 1, "away_score": 1},
    )
    assert response.status_code == 403


def test_admin_override_keeps_finished_and_updates_scores(client: TestClient) -> None:
    finished = _seed(
        client,
        external_id="override-finished",
        kickoff=datetime.now(UTC) - timedelta(days=1),
        status=MatchStatus.FINISHED,
    )
    assert finished.id is not None
    admin = _admin_headers()

    first = client.post(
        f"/admin/matches/{finished.id}/result",
        headers=admin,
        json={"home_score": 1, "away_score": 0},
    )
    assert first.status_code == 200, first.text

    second = client.post(
        f"/admin/matches/{finished.id}/result",
        headers=admin,
        json={"home_score": 4, "away_score": 2},
    )
    assert second.status_code == 200, second.text
    body = second.json()
    assert body["status"] == MatchStatus.FINISHED
    assert body["home_score"] == 4
    assert body["away_score"] == 2


def test_admin_override_unknown_match_404(client: TestClient) -> None:
    admin = _admin_headers()
    response = client.post(
        "/admin/matches/999999/result",
        headers=admin,
        json={"home_score": 1, "away_score": 0},
    )
    assert response.status_code == 404


def test_admin_override_boundary_exactly_two_hours(client: TestClient) -> None:
    """Exactly kickoff+2h is allowed (>=)."""
    fixture = _seed(
        client,
        external_id="override-exact-2h",
        kickoff=datetime.now(UTC) - timedelta(hours=2, seconds=2),
    )
    assert fixture.id is not None
    admin = _admin_headers()

    response = client.post(
        f"/admin/matches/{fixture.id}/result",
        headers=admin,
        json={"home_score": 1, "away_score": 1},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == MatchStatus.FINISHED
