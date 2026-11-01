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


def _kickoff(day: date, hour: int = 15) -> datetime:
    return datetime(day.year, day.month, day.day, hour, 0, tzinfo=UTC)


def _ingest(session: AsyncSession) -> MatchIngestService:
    leagues = LeagueService(SqlAlchemyLeagueRepository(session), SqlAlchemyMatchRepository(session))
    matches = MatchService(SqlAlchemyMatchRepository(session), leagues)
    return MatchIngestService(matches, leagues)


async def _seed_weekend(session_factory: async_sessionmaker[AsyncSession]) -> Match:
    async with session_factory() as session:
        ingest = _ingest(session)
        league = await ingest.upsert_league(
            League(
                name="Premier League",
                code="PL",
                start_date=date(2025, 8, 1),
                current_matchday=10,
            )
        )
        assert league.id is not None

        day = date(2026, 3, 14)
        history = [
            ("hist-1", "Arsenal", "Chelsea", 2, 1, day - timedelta(days=28)),
            ("hist-2", "Chelsea", "Arsenal", 0, 0, day - timedelta(days=21)),
            ("hist-3", "Arsenal", "Brighton", 3, 0, day - timedelta(days=14)),
            ("hist-4", "Liverpool", "Chelsea", 1, 0, day - timedelta(days=14)),
            ("hist-5", "Chelsea", "Everton", 2, 2, day - timedelta(days=7)),
            ("hist-6", "Arsenal", "Everton", 1, 0, day - timedelta(days=7)),
        ]
        for external_id, home, away, home_score, away_score, when in history:
            await ingest.upsert_match(
                Match(
                    external_id=external_id,
                    league_id=league.id,
                    matchday=1,
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
                external_id="pl-ars-che-2026",
                league_id=league.id,
                matchday=10,
                match_date=_kickoff(day),
                status=MatchStatus.SCHEDULED,
                home_team="Arsenal",
                away_team="Chelsea",
            )
        )
        await ingest.upsert_match(
            Match(
                external_id="pl-liv-mci-2026",
                league_id=league.id,
                matchday=10,
                match_date=_kickoff(day, hour=17),
                status=MatchStatus.SCHEDULED,
                home_team="Liverpool",
                away_team="Man City",
            )
        )
        assert fixture.id is not None
        return fixture


def test_available_matches_and_match_detail(client: TestClient) -> None:
    fixture = anyio.run(_seed_weekend, client.app.state.session_factory)
    assert fixture.id is not None

    token, _ = make_access_token(roles=["ADMIN"])
    headers = auth_header(token)

    select = client.post(
        "/admin/addmatch",
        headers=headers,
        json={"id": fixture.id, "game_number": 1},
    )
    assert select.status_code == 200, select.text
    assert select.json()["use_match"] is True
    assert select.json()["game_number"] == 1

    available = client.get("/game/livematches", params={"matchDate": "14-03-2026"})
    assert available.status_code == 200, available.text
    payload = available.json()["matches"]
    assert len(payload) == 1
    assert payload[0]["home_team"] == "Arsenal"
    assert payload[0]["away_team"] == "Chelsea"
    assert payload[0]["game_number"] == 1

    admin_list = client.get(
        "/admin/listmatches",
        headers=headers,
        params={"matchDate": "14-03-2026"},
    )
    assert admin_list.status_code == 200
    assert len(admin_list.json()["matches"]) == 2

    detail = client.get(
        "/game/matchdetail",
        params={
            "hometeam": "Arsenal",
            "awayteam": "Chelsea",
            "matchdate": "14-03-2026",
        },
    )
    assert detail.status_code == 200, detail.text
    body = detail.json()["match_detail"]
    assert body["league_position"]["Arsenal"] == "1"
    assert body["league_position"]["Chelsea"] == "3"
    assert body["team_performance"]["Arsenal"] == ["WIN", "DRAW", "WIN", "WIN"]
    assert body["head_to_head"]["Arsenal"] == ["WIN", "DRAW"]
    assert body["head_to_head"]["Chelsea"] == ["LOSE", "DRAW"]

    async def update_score() -> Match:
        async with client.app.state.session_factory() as session:
            ingest = _ingest(session)
            return await ingest.update_score(
                external_id="pl-ars-che-2026",
                status=MatchStatus.FINISHED,
                home_score=2,
                away_score=1,
                match_minute=90,
            )

    updated = anyio.run(update_score)
    assert updated.home_score == 2
    assert updated.use_match is True
    assert updated.game_number == 1


def test_livematches_requires_match_date(client: TestClient) -> None:
    response = client.get("/game/livematches")
    assert response.status_code == 400
    assert response.json()["detail"] == "missing matchDate"


def test_matchdetail_not_found(client: TestClient) -> None:
    response = client.get(
        "/game/matchdetail",
        params={"hometeam": "A", "awayteam": "B", "matchdate": "01-01-2026"},
    )
    assert response.status_code == 404
