from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from baked.superiorsix.domain.models import League, MatchStatus
from baked.superiorsix.process.extractors.connectors.dto import LeaguePayload, MatchPayload
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

    def collect_matches(self, league_code: str, match_week: int, *, season_start=None):
        raise AssertionError("not used")


class StubApiFootball:
    provider = "api-football"

    def __init__(
        self,
        *,
        configured: bool = True,
        leagues: list[LeaguePayload] | None = None,
    ) -> None:
        self.configured = configured
        self._leagues = leagues or []

    def collect_leagues(self) -> list[LeaguePayload]:
        return list(self._leagues)

    def collect_matches(self, league_code: str, match_week: int, *, season_start=None):
        raise AssertionError("not used")


class StubMatchConnector:
    provider = "stub"

    def __init__(self, matches: list[MatchPayload]) -> None:
        self._matches = matches

    def collect_leagues(self):
        return []

    def collect_matches(self, league_code: str, match_week: int, *, season_start=None):
        assert league_code == "PL"
        assert match_week == 10
        return list(self._matches)


def _services(session: AsyncSession) -> tuple[MatchIngestService, LeagueService]:
    leagues = LeagueService(SqlAlchemyLeagueRepository(session), SqlAlchemyMatchRepository(session))
    matches = MatchService(SqlAlchemyMatchRepository(session), leagues)
    return MatchIngestService(matches, leagues), leagues


@pytest.mark.asyncio
async def test_league_extractor_upserts_from_both_providers(
    client,
) -> None:
    session_factory: async_sessionmaker[AsyncSession] = client.app.state.session_factory
    async with session_factory() as session:
        ingest, _ = _services(session)
        extractor = LeagueExtractor(
            ingest,
            football_data_token="fd",
            api_football_key="af",
            include_api_football=True,
        )
        extractor._football_data = StubFootballData(  # noqa: SLF001
            [
                LeaguePayload(
                    name="Premier League",
                    code="PL",
                    start_date=date(2025, 8, 1),
                    current_matchday=9,
                )
            ]
        )
        extractor._api_football = StubApiFootball(  # noqa: SLF001
            leagues=[
                LeaguePayload(
                    name="League One",
                    code="EL1",
                    start_date=date(2025, 8, 1),
                    current_matchday=4,
                )
            ]
        )
        leagues = await extractor.process()
        await session.commit()

    assert {league.code for league in leagues} == {"PL", "EL1"}
    async with session_factory() as session:
        _, league_service = _services(session)
        el1 = await league_service.get_by_code("EL1")
        assert el1.current_matchday == 4


@pytest.mark.asyncio
async def test_match_extractor_requires_exact_week_and_upserts(
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
                current_matchday=10,
            )
        )
        await session.commit()

    stub = StubMatchConnector(
        [
            MatchPayload(
                external_id="fd-1",
                matchday=10,
                match_date=datetime(2025, 11, 1, 15, 0, tzinfo=UTC),
                status=MatchStatus.SCHEDULED,
                home_team="Arsenal",
                away_team="Chelsea",
            ),
            MatchPayload(
                external_id="fd-2",
                matchday=10,
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
            match_week=10,
            football_data_token="fd",
            api_football_key=None,
        )
        matches = await extractor.process()
        await session.commit()

    assert len(matches) == 2
    finished = next(match for match in matches if match.external_id == "fd-2")
    assert finished.home_score == 1
    assert finished.status == MatchStatus.FINISHED

    with pytest.raises(ValueError, match="match_week"):
        MatchExtractor(
            ingest,
            leagues,
            league_code="PL",
            match_week=0,
            football_data_token="fd",
            api_football_key=None,
        )
