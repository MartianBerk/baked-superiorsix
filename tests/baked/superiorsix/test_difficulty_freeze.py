"""Difficulty is immutable once computed (SS-125)."""

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


def _match_service(session: AsyncSession) -> MatchService:
    leagues = LeagueService(SqlAlchemyLeagueRepository(session), SqlAlchemyMatchRepository(session))
    return MatchService(SqlAlchemyMatchRepository(session), leagues)


def _fmt(day: date) -> str:
    return day.strftime("%d-%m-%Y")


async def _seed_fixture(
    session_factory: async_sessionmaker[AsyncSession],
) -> tuple[Match, date]:
    async with session_factory() as session:
        ingest = _ingest(session)
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
        history = [
            ("freeze-h1", "Arsenal", "Chelsea", 2, 1, day - timedelta(days=28)),
            ("freeze-h2", "Chelsea", "Arsenal", 0, 0, day - timedelta(days=21)),
            ("freeze-h3", "Arsenal", "Brighton", 3, 0, day - timedelta(days=14)),
            ("freeze-h4", "Liverpool", "Chelsea", 1, 0, day - timedelta(days=14)),
            ("freeze-h5", "Chelsea", "Everton", 2, 2, day - timedelta(days=7)),
            ("freeze-h6", "Arsenal", "Everton", 1, 0, day - timedelta(days=7)),
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
                external_id="freeze-ars-che",
                league_id=league.id,
                match_date=_kickoff(day),
                status=MatchStatus.SCHEDULED,
                home_team="Arsenal",
                away_team="Chelsea",
            )
        )
        assert fixture.id is not None
        assert fixture.difficulty_score is None
        assert fixture.difficulty_band is None
        return fixture, day


def test_difficulty_set_once_survives_finished_ingest(client: TestClient) -> None:
    fixture, day = anyio.run(_seed_fixture, client.app.state.session_factory)
    assert fixture.id is not None

    token, _ = make_access_token(roles=["admin"])
    headers = auth_header(token)
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
    row = next(m for m in listed.json()["matches"] if m["id"] == fixture.id)
    assert row["difficulty"] is not None
    frozen = row["difficulty"]
    assert 0.0 <= frozen["score"] <= 1.0
    assert 1 <= frozen["band"] <= 5

    async def reload() -> Match:
        async with client.app.state.session_factory() as session:
            stored = await SqlAlchemyMatchRepository(session).get(fixture.id)
            assert stored is not None
            return stored

    stored = anyio.run(reload)
    assert stored.difficulty_score == frozen["score"]
    assert stored.difficulty_band == frozen["band"]

    async def finish_and_recompute() -> tuple[Match, object]:
        async with client.app.state.session_factory() as session:
            ingest = _ingest(session)
            finished = await ingest.update_score(
                external_id="freeze-ars-che",
                status=MatchStatus.FINISHED,
                home_score=5,
                away_score=0,
                match_minute=90,
            )
            # Blow up the league table further with another result involving Arsenal.
            assert finished.league_id is not None
            await ingest.upsert_match(
                Match(
                    external_id="freeze-ars-blowout",
                    league_id=finished.league_id,
                    match_date=_kickoff(day - timedelta(days=1)),
                    status=MatchStatus.FINISHED,
                    home_team="Arsenal",
                    away_team="Wolves",
                    home_score=4,
                    away_score=0,
                    match_minute=90,
                )
            )
            assert finished.id is not None
            service = _match_service(session)
            refreshed = await service.get(finished.id)
            difficulty = await service.difficulty_for_match(refreshed)
            return finished, difficulty

    finished, difficulty = anyio.run(finish_and_recompute)
    assert finished.status == MatchStatus.FINISHED
    assert finished.home_score == 5
    assert finished.difficulty_score == frozen["score"]
    assert finished.difficulty_band == frozen["band"]
    assert difficulty.score == frozen["score"]
    assert difficulty.band == frozen["band"]

    again = client.get(
        "/admin/listmatches",
        headers=headers,
        params={"fromDate": _fmt(day), "toDate": _fmt(day)},
    )
    assert again.status_code == 200
    row_again = next(m for m in again.json()["matches"] if m["id"] == fixture.id)
    assert row_again["difficulty"] == frozen

    detail = client.get("/game/matchdetail", params={"id": fixture.id})
    assert detail.status_code == 200, detail.text
    assert detail.json()["match_detail"]["difficulty"] == frozen


def test_upsert_preserves_frozen_difficulty(client: TestClient) -> None:
    fixture, _day = anyio.run(_seed_fixture, client.app.state.session_factory)
    assert fixture.id is not None

    async def freeze_then_upsert() -> tuple[float, int, Match]:
        async with client.app.state.session_factory() as session:
            service = _match_service(session)
            match = await service.get(fixture.id)
            first = await service.difficulty_for_match(match)
            ingest = _ingest(session)
            upserted = await ingest.upsert_match(
                Match(
                    external_id="freeze-ars-che",
                    league_id=match.league_id,
                    match_date=match.match_date,
                    status=MatchStatus.IN_PLAY,
                    home_team="Arsenal",
                    away_team="Chelsea",
                    home_score=1,
                    away_score=0,
                    match_minute=33,
                    # Attempt to overwrite with nonsense — must be ignored.
                    difficulty_score=0.001,
                    difficulty_band=1,
                )
            )
            return first.score, first.band, upserted

    score, band, upserted = anyio.run(freeze_then_upsert)
    assert upserted.status == MatchStatus.IN_PLAY
    assert upserted.home_score == 1
    assert upserted.difficulty_score == score
    assert upserted.difficulty_band == band


def test_selection_freezes_difficulty(client: TestClient) -> None:
    fixture, _day = anyio.run(_seed_fixture, client.app.state.session_factory)
    assert fixture.id is not None

    token, _ = make_access_token(roles=["admin"])
    headers = auth_header(token)
    opened = client.post(
        "/admin/addround",
        headers=headers,
        json={"start_date": date.today().isoformat(), "buy_in_pence": 100},
    )
    assert opened.status_code == 200, opened.text

    selected = client.post(
        "/admin/addmatch",
        headers=headers,
        json={"id": fixture.id, "game_number": 1},
    )
    assert selected.status_code == 200, selected.text

    async def stored_difficulty() -> Match:
        async with client.app.state.session_factory() as session:
            match = await SqlAlchemyMatchRepository(session).get(fixture.id)
            assert match is not None
            return match

    match = anyio.run(stored_difficulty)
    assert match.use_match is True
    assert match.difficulty_score is not None
    assert match.difficulty_band is not None
    frozen_score = match.difficulty_score
    frozen_band = match.difficulty_band

    async def finish() -> Match:
        async with client.app.state.session_factory() as session:
            ingest = _ingest(session)
            return await ingest.update_score(
                external_id="freeze-ars-che",
                status=MatchStatus.FINISHED,
                home_score=0,
                away_score=3,
                match_minute=90,
            )

    finished = anyio.run(finish)
    assert finished.difficulty_score == frozen_score
    assert finished.difficulty_band == frozen_band
