from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from baked.superiorsix.domain.models import League, Match, MatchStatus
from baked.superiorsix.process.extractors.connectors.dto import LeaguePayload, MatchPayload
from baked.superiorsix.process.extractors.cli import (
    DEFAULT_MATCH_EXTRACTOR_DAYS_AHEAD,
    resolve_match_extractor_date_range,
)
from baked.superiorsix.process.extractors.league import LeagueExtractor
from baked.superiorsix.process.extractors.match import MatchExtractor
from baked.superiorsix.repositories.leagues import SqlAlchemyLeagueRepository
from baked.superiorsix.repositories.matches import SqlAlchemyMatchRepository
from baked.superiorsix.services.ingest import MatchIngestService
from baked.superiorsix.services.leagues import LeagueService
from baked.superiorsix.services.matches import MatchService


class StubFootballData:
    provider = "football-data.org"

    def __init__(self, leagues: list[LeaguePayload] | None = None) -> None:
        self._leagues = leagues or []

    def collect_leagues(self) -> list[LeaguePayload]:
        return list(self._leagues)

    def collect_matches(
        self, league_code: str, *, date_from, date_to, season_start=None, live=False
    ):
        raise AssertionError("not used")


class StubMatchConnector:
    provider = "stub"

    def __init__(self, matches: list[MatchPayload]) -> None:
        self._matches = matches

    def collect_leagues(self):
        return []

    def collect_matches(
        self, league_code: str, *, date_from, date_to, season_start=None, live=False
    ):
        assert league_code == "PL"
        assert date_from == date(2025, 11, 1)
        assert date_to == date(2025, 11, 2)
        assert live is False
        return list(self._matches)


def test_resolve_match_extractor_date_range_explicit_to() -> None:
    start = date(2026, 11, 11)
    end = date(2026, 11, 15)
    resolved_from, resolved_to, fallback = resolve_match_extractor_date_range(
        date_from=start,
        date_to=end,
        days_ahead=None,
        latest_match_date=date(2026, 11, 1),
        today_utc=date(2026, 11, 11),
    )
    assert (resolved_from, resolved_to, fallback) == (start, end, False)


def test_resolve_match_extractor_date_range_from_latest_and_days_ahead() -> None:
    latest = date(2026, 11, 10)
    resolved_from, resolved_to, fallback = resolve_match_extractor_date_range(
        date_from=None,
        date_to=None,
        days_ahead=7,
        latest_match_date=latest,
        today_utc=date(2026, 11, 11),
    )
    assert resolved_from == latest
    assert resolved_to == latest + timedelta(days=7)
    assert fallback is False


def test_resolve_match_extractor_date_range_only_to_uses_latest_from() -> None:
    latest = date(2026, 11, 10)
    end = date(2026, 11, 20)
    resolved_from, resolved_to, fallback = resolve_match_extractor_date_range(
        date_from=None,
        date_to=end,
        days_ahead=None,
        latest_match_date=latest,
        today_utc=date(2026, 11, 11),
    )
    assert (resolved_from, resolved_to, fallback) == (latest, end, False)


def test_resolve_match_extractor_date_range_no_matches_uses_today() -> None:
    today = date(2026, 11, 11)
    resolved_from, resolved_to, fallback = resolve_match_extractor_date_range(
        date_from=None,
        date_to=None,
        days_ahead=None,
        latest_match_date=None,
        today_utc=today,
    )
    assert resolved_from == today
    assert resolved_to == today + timedelta(days=DEFAULT_MATCH_EXTRACTOR_DAYS_AHEAD)
    assert fallback is True


def test_resolve_match_extractor_date_range_from_only_default_span() -> None:
    start = date(2026, 11, 11)
    resolved_from, resolved_to, fallback = resolve_match_extractor_date_range(
        date_from=start,
        date_to=None,
        days_ahead=None,
        latest_match_date=None,
        today_utc=start,
    )
    assert resolved_from == start
    assert resolved_to == start + timedelta(days=DEFAULT_MATCH_EXTRACTOR_DAYS_AHEAD)
    assert fallback is False


def test_resolve_match_extractor_date_range_rejects_to_before_from() -> None:
    with pytest.raises(ValueError, match="--to must be on or after --from"):
        resolve_match_extractor_date_range(
            date_from=date(2026, 11, 15),
            date_to=date(2026, 11, 10),
            days_ahead=None,
            latest_match_date=None,
            today_utc=date(2026, 11, 11),
        )


def _services(session: AsyncSession) -> tuple[MatchIngestService, LeagueService]:
    leagues = LeagueService(SqlAlchemyLeagueRepository(session), SqlAlchemyMatchRepository(session))
    matches = MatchService(SqlAlchemyMatchRepository(session), leagues)
    return MatchIngestService(matches, leagues), leagues


@pytest.mark.asyncio
async def test_league_extractor_upserts_from_football_data(
    client,
) -> None:
    session_factory: async_sessionmaker[AsyncSession] = client.app.state.session_factory
    async with session_factory() as session:
        ingest, _ = _services(session)
        extractor = LeagueExtractor(
            ingest,
            football_data_token="fd",
        )
        extractor._football_data = StubFootballData(  # noqa: SLF001
            [
                LeaguePayload(
                    name="Premier League",
                    code="PL",
                    start_date=date(2025, 8, 1),
                ),
                LeaguePayload(
                    name="League One",
                    code="EL1",
                    start_date=date(2025, 8, 1),
                ),
            ]
        )
        leagues = await extractor.process()
        await session.commit()

    assert {league.code for league in leagues} == {"PL", "EL1"}
    async with session_factory() as session:
        _, league_service = _services(session)
        el1 = await league_service.get_by_code("EL1")
        assert el1.name == "League One"


@pytest.mark.asyncio
async def test_match_extractor_uses_date_range_and_upserts(
    client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_factory: async_sessionmaker[AsyncSession] = client.app.state.session_factory
    async with session_factory() as session:
        ingest, leagues = _services(session)
        await ingest.upsert_league(
            League(
                name="Premier League",
                code="PL",
                start_date=date(2025, 8, 1),
            )
        )
        await session.commit()

    stub = StubMatchConnector(
        [
            MatchPayload(
                external_id="fd-1",
                match_date=datetime(2025, 11, 1, 15, 0, tzinfo=UTC),
                status=MatchStatus.SCHEDULED,
                home_team="Arsenal",
                away_team="Chelsea",
            ),
            MatchPayload(
                external_id="fd-2",
                match_date=datetime(2025, 11, 1, 17, 0, tzinfo=UTC),
                status=MatchStatus.FINISHED,
                home_team="Liverpool",
                away_team="Everton",
                home_score=1,
                away_score=0,
                match_minute=90,
            ),
        ]
    )
    monkeypatch.setattr(
        "baked.superiorsix.process.extractors.match.connector_for_league",
        lambda *args, **kwargs: stub,
    )

    async with session_factory() as session:
        ingest, leagues = _services(session)
        extractor = MatchExtractor(
            ingest,
            leagues,
            league_code="PL",
            date_from=date(2025, 11, 1),
            date_to=date(2025, 11, 2),
            football_data_token="fd",
            goal_api_key=None,
        )
        matches = await extractor.process()
        await session.commit()

    assert len(matches) == 2
    finished = next(match for match in matches if match.external_id == "fd-2")
    assert finished.home_score == 1
    assert finished.status == MatchStatus.FINISHED

    with pytest.raises(ValueError, match="date_to"):
        MatchExtractor(
            ingest,
            leagues,
            league_code="PL",
            date_from=date(2025, 11, 2),
            date_to=date(2025, 11, 1),
            football_data_token="fd",
            goal_api_key=None,
        )


@pytest.mark.asyncio
async def test_max_match_date_for_league_code(client) -> None:
    session_factory: async_sessionmaker[AsyncSession] = client.app.state.session_factory
    async with session_factory() as session:
        ingest, _ = _services(session)
        pl = await ingest.upsert_league(
            League(
                name="Premier League",
                code="PL",
                start_date=date(2025, 8, 1),
            )
        )
        await ingest.upsert_league(
            League(
                name="League One",
                code="EL1",
                start_date=date(2025, 8, 1),
            )
        )
        await ingest.upsert_match(
            Match(
                external_id="pl-old",
                league_id=pl.id,
                match_date=datetime(2025, 10, 1, 15, 0, tzinfo=UTC),
                status=MatchStatus.SCHEDULED,
                home_team="A",
                away_team="B",
            )
        )
        await ingest.upsert_match(
            Match(
                external_id="pl-new",
                league_id=pl.id,
                match_date=datetime(2025, 11, 3, 15, 0, tzinfo=UTC),
                status=MatchStatus.SCHEDULED,
                home_team="C",
                away_team="D",
            )
        )
        await session.commit()

    async with session_factory() as session:
        repo = SqlAlchemyMatchRepository(session)
        assert await repo.max_match_date_for_league_code("PL") == date(2025, 11, 3)
        assert await repo.max_match_date_for_league_code("EL1") is None
