"""Forward-only match status coalescing (SS-124)."""

from datetime import UTC, date, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from baked.superiorsix.domain.match_status import (
    coalesce_forward_status,
    is_status_regression,
    status_rank,
)
from baked.superiorsix.domain.models import League, Match, MatchStatus
from baked.superiorsix.repositories.leagues import SqlAlchemyLeagueRepository
from baked.superiorsix.repositories.matches import SqlAlchemyMatchRepository
from baked.superiorsix.services.ingest import MatchIngestService
from baked.superiorsix.services.leagues import LeagueService
from baked.superiorsix.services.matches import MatchService


@pytest.mark.parametrize(
    ("current", "incoming", "expected"),
    [
        (MatchStatus.SCHEDULED, MatchStatus.IN_PLAY, MatchStatus.IN_PLAY),
        (MatchStatus.SCHEDULED, MatchStatus.HALF_TIME, MatchStatus.HALF_TIME),
        (MatchStatus.SCHEDULED, MatchStatus.FINISHED, MatchStatus.FINISHED),
        (MatchStatus.SCHEDULED, MatchStatus.POSTPONED, MatchStatus.POSTPONED),
        (MatchStatus.POSTPONED, MatchStatus.SCHEDULED, MatchStatus.SCHEDULED),
        (MatchStatus.POSTPONED, MatchStatus.FINISHED, MatchStatus.FINISHED),
        (MatchStatus.IN_PLAY, MatchStatus.HALF_TIME, MatchStatus.HALF_TIME),
        (MatchStatus.HALF_TIME, MatchStatus.IN_PLAY, MatchStatus.IN_PLAY),
        (MatchStatus.IN_PLAY, MatchStatus.FINISHED, MatchStatus.FINISHED),
        (MatchStatus.HALF_TIME, MatchStatus.FINISHED, MatchStatus.FINISHED),
        (MatchStatus.FINISHED, MatchStatus.FINISHED, MatchStatus.FINISHED),
        (MatchStatus.IN_PLAY, MatchStatus.IN_PLAY, MatchStatus.IN_PLAY),
    ],
)
def test_coalesce_allows_forward_and_sideways(
    current: str,
    incoming: str,
    expected: str,
) -> None:
    assert coalesce_forward_status(current, incoming) == expected
    assert not is_status_regression(current, incoming)


@pytest.mark.parametrize(
    ("current", "incoming"),
    [
        (MatchStatus.FINISHED, MatchStatus.IN_PLAY),
        (MatchStatus.FINISHED, MatchStatus.HALF_TIME),
        (MatchStatus.FINISHED, MatchStatus.SCHEDULED),
        (MatchStatus.FINISHED, MatchStatus.POSTPONED),
        (MatchStatus.IN_PLAY, MatchStatus.SCHEDULED),
        (MatchStatus.HALF_TIME, MatchStatus.SCHEDULED),
        (MatchStatus.IN_PLAY, MatchStatus.POSTPONED),
        (MatchStatus.HALF_TIME, MatchStatus.POSTPONED),
    ],
)
def test_coalesce_rejects_regressions(current: str, incoming: str) -> None:
    assert is_status_regression(current, incoming)
    assert coalesce_forward_status(current, incoming) == current


def test_finished_outranks_live_and_scheduled() -> None:
    assert status_rank(MatchStatus.FINISHED) > status_rank(MatchStatus.IN_PLAY)
    assert status_rank(MatchStatus.IN_PLAY) == status_rank(MatchStatus.HALF_TIME)
    assert status_rank(MatchStatus.SCHEDULED) == status_rank(MatchStatus.POSTPONED)


def _ingest(session: AsyncSession) -> MatchIngestService:
    leagues = LeagueService(SqlAlchemyLeagueRepository(session), SqlAlchemyMatchRepository(session))
    matches = MatchService(SqlAlchemyMatchRepository(session), leagues)
    return MatchIngestService(matches, leagues)


async def _seed_finished(
    session_factory: async_sessionmaker[AsyncSession],
) -> Match:
    async with session_factory() as session:
        ingest = _ingest(session)
        league = await ingest.upsert_league(
            League(name="Premier League", code="PL", start_date=date(2025, 8, 1))
        )
        assert league.id is not None
        match = await ingest.upsert_match(
            Match(
                external_id="fd-forward-1",
                league_id=league.id,
                match_date=datetime(2025, 11, 7, 15, 0, tzinfo=UTC),
                status=MatchStatus.FINISHED,
                home_team="Arsenal",
                away_team="Chelsea",
                home_score=2,
                away_score=1,
                match_minute=90,
            )
        )
        await session.commit()
        assert match.id is not None
        return match


@pytest.mark.asyncio
async def test_upsert_rejects_finished_to_scheduled(client) -> None:
    session_factory: async_sessionmaker[AsyncSession] = client.app.state.session_factory
    finished = await _seed_finished(session_factory)

    async with session_factory() as session:
        ingest = _ingest(session)
        updated = await ingest.upsert_match(
            Match(
                external_id="fd-forward-1",
                league_id=finished.league_id,
                match_date=datetime(2025, 11, 7, 15, 0, tzinfo=UTC),
                status=MatchStatus.SCHEDULED,
                home_team="Arsenal",
                away_team="Chelsea",
                home_score=None,
                away_score=None,
                match_minute=None,
            )
        )
        await session.commit()

    assert updated.status == MatchStatus.FINISHED
    assert updated.home_score == 2
    assert updated.away_score == 1
    assert updated.match_minute == 90


@pytest.mark.asyncio
async def test_upsert_rejects_finished_to_postponed(client) -> None:
    session_factory: async_sessionmaker[AsyncSession] = client.app.state.session_factory
    finished = await _seed_finished(session_factory)

    async with session_factory() as session:
        ingest = _ingest(session)
        updated = await ingest.upsert_match(
            Match(
                external_id="fd-forward-1",
                league_id=finished.league_id,
                match_date=datetime(2025, 11, 7, 15, 0, tzinfo=UTC),
                status=MatchStatus.POSTPONED,
                home_team="Arsenal",
                away_team="Chelsea",
                home_score=2,
                away_score=1,
                match_minute=90,
            )
        )
        await session.commit()

    assert updated.status == MatchStatus.FINISHED


@pytest.mark.asyncio
async def test_upsert_allows_scheduled_to_in_play(client) -> None:
    session_factory: async_sessionmaker[AsyncSession] = client.app.state.session_factory
    async with session_factory() as session:
        ingest = _ingest(session)
        league = await ingest.upsert_league(
            League(name="Premier League", code="PL", start_date=date(2025, 8, 1))
        )
        assert league.id is not None
        league_id = league.id
        await ingest.upsert_match(
            Match(
                external_id="fd-forward-2",
                league_id=league_id,
                match_date=datetime(2025, 11, 7, 15, 0, tzinfo=UTC),
                status=MatchStatus.SCHEDULED,
                home_team="Liverpool",
                away_team="Everton",
            )
        )
        await session.commit()

    async with session_factory() as session:
        ingest = _ingest(session)
        updated = await ingest.upsert_match(
            Match(
                external_id="fd-forward-2",
                league_id=league_id,
                match_date=datetime(2025, 11, 7, 15, 0, tzinfo=UTC),
                status=MatchStatus.IN_PLAY,
                home_team="Liverpool",
                away_team="Everton",
                home_score=0,
                away_score=0,
                match_minute=12,
            )
        )
        await session.commit()

    assert updated.status == MatchStatus.IN_PLAY
    assert updated.home_score == 0
    assert updated.match_minute == 12


@pytest.mark.asyncio
async def test_apply_score_update_rejects_finished_to_in_play(client) -> None:
    session_factory: async_sessionmaker[AsyncSession] = client.app.state.session_factory
    finished = await _seed_finished(session_factory)

    async with session_factory() as session:
        matches = MatchService(
            SqlAlchemyMatchRepository(session),
            LeagueService(
                SqlAlchemyLeagueRepository(session),
                SqlAlchemyMatchRepository(session),
            ),
        )
        updated = await matches.apply_score_update(
            match_id=finished.id,
            status=MatchStatus.IN_PLAY,
            home_score=3,
            away_score=1,
            match_minute=88,
        )
        await session.commit()

    assert updated.status == MatchStatus.FINISHED
    assert updated.home_score == 3
    assert updated.away_score == 1
    assert updated.match_minute == 88


@pytest.mark.asyncio
async def test_apply_score_update_allows_half_time_to_in_play(client) -> None:
    session_factory: async_sessionmaker[AsyncSession] = client.app.state.session_factory
    async with session_factory() as session:
        ingest = _ingest(session)
        league = await ingest.upsert_league(
            League(name="Premier League", code="PL", start_date=date(2025, 8, 1))
        )
        assert league.id is not None
        match = await ingest.upsert_match(
            Match(
                external_id="fd-forward-3",
                league_id=league.id,
                match_date=datetime(2025, 11, 7, 15, 0, tzinfo=UTC),
                status=MatchStatus.HALF_TIME,
                home_team="Spurs",
                away_team="West Ham",
                home_score=1,
                away_score=0,
                match_minute=45,
            )
        )
        await session.commit()
        match_id = match.id

    async with session_factory() as session:
        matches = MatchService(
            SqlAlchemyMatchRepository(session),
            LeagueService(
                SqlAlchemyLeagueRepository(session),
                SqlAlchemyMatchRepository(session),
            ),
        )
        updated = await matches.apply_score_update(
            match_id=match_id,
            status=MatchStatus.IN_PLAY,
            home_score=1,
            away_score=0,
            match_minute=46,
        )
        await session.commit()

    assert updated.status == MatchStatus.IN_PLAY
    assert updated.match_minute == 46
