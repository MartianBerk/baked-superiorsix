from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from baked.superiorsix.domain.models import League, MatchStatus
from baked.superiorsix.process.extractors.connectors.dto import LeaguePayload, MatchPayload
from baked.superiorsix.process.extractors.connectors.football_data import (
    TOURNAMENT_CATALOG_CODES,
    FootballDataConnector,
)
from baked.superiorsix.process.extractors.tournament import TournamentExtractor
from baked.superiorsix.repositories.leagues import SqlAlchemyLeagueRepository
from baked.superiorsix.repositories.matches import SqlAlchemyMatchRepository
from baked.superiorsix.services.ingest import MatchIngestService
from baked.superiorsix.services.leagues import LeagueService
from baked.superiorsix.services.matches import MatchService


class StubTournamentFootballData:
    provider = "football-data.org"

    def __init__(self, leagues: list[LeaguePayload]) -> None:
        self._leagues = leagues

    def collect_tournaments(self, codes=None):
        wanted = frozenset(codes or TOURNAMENT_CATALOG_CODES)
        return [league for league in self._leagues if league.code in wanted]

    def collect_leagues(self):
        return list(self._leagues)

    def collect_matches(self, *args, **kwargs):
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
        assert league_code == "FAC"
        assert live is False
        return list(self._matches)


def _services(session: AsyncSession) -> tuple[MatchIngestService, LeagueService]:
    leagues = LeagueService(SqlAlchemyLeagueRepository(session), SqlAlchemyMatchRepository(session))
    matches = MatchService(SqlAlchemyMatchRepository(session), leagues)
    return MatchIngestService(matches, leagues), leagues


def test_collect_tournaments_splits_england_and_world_areas() -> None:
    routes = {
        (
            "https://api.football-data.org/v2/competitions",
            (("areas", 2072),),
        ): {
            "competitions": [
                {
                    "name": "FA Cup",
                    "code": "FAC",
                    "currentSeason": {"startDate": "2025-08-01"},
                },
                {
                    "name": "Premier League",
                    "code": "PL",
                    "currentSeason": {"startDate": "2025-08-01"},
                },
            ]
        },
        (
            "https://api.football-data.org/v2/competitions",
            (("areas", 2267),),
        ): {
            "competitions": [
                {
                    "name": "European Championship",
                    "code": "EC",
                    "currentSeason": {"startDate": "2024-06-14"},
                },
                {
                    "name": "FIFA World Cup",
                    "code": "WC",
                    "currentSeason": {"startDate": "2022-11-20"},
                },
            ]
        },
    }

    class FakeHttp:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict | None]] = []

        def get_json(self, url, *, headers=None, params=None):
            del headers
            self.calls.append((url, params))
            key = (url, tuple(sorted((params or {}).items())))
            return routes[key]

    http = FakeHttp()
    connector = FootballDataConnector("token", http=http)
    leagues = connector.collect_tournaments()
    assert {league.code for league in leagues} == {"FAC", "EC", "WC"}
    assert len(http.calls) == 2


@pytest.mark.asyncio
async def test_tournament_catalog_upserts(client) -> None:
    session_factory: async_sessionmaker[AsyncSession] = client.app.state.session_factory
    async with session_factory() as session:
        ingest, leagues = _services(session)
        extractor = TournamentExtractor(
            ingest,
            leagues,
            football_data_token="fd",
            codes=frozenset({"FAC", "EC"}),
        )
        extractor._football_data = StubTournamentFootballData(  # noqa: SLF001
            [
                LeaguePayload(name="FA Cup", code="FAC", start_date=date(2025, 8, 1)),
                LeaguePayload(
                    name="European Championship",
                    code="EC",
                    start_date=date(2024, 6, 14),
                ),
            ]
        )
        upserted = await extractor.process_catalog()
        await session.commit()

    assert {league.code for league in upserted} == {"FAC", "EC"}
    async with session_factory() as session:
        _, league_service = _services(session)
        fac = await league_service.get_by_code("FAC")
        assert fac.name == "FA Cup"


@pytest.mark.asyncio
async def test_tournament_match_pull_reuses_match_extractor(
    client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_factory: async_sessionmaker[AsyncSession] = client.app.state.session_factory
    async with session_factory() as session:
        ingest, leagues = _services(session)
        await ingest.upsert_league(
            League(name="FA Cup", code="FAC", start_date=date(2025, 8, 1))
        )
        await session.commit()

    stub = StubMatchConnector(
        [
            MatchPayload(
                external_id="fd-fac-1",
                match_date=datetime(2026, 1, 10, 15, 0, tzinfo=UTC),
                status=MatchStatus.SCHEDULED,
                home_team="Arsenal",
                away_team="Man City",
            )
        ]
    )
    monkeypatch.setattr(
        "baked.superiorsix.process.extractors.match.connector_for_league",
        lambda *args, **kwargs: stub,
    )

    async with session_factory() as session:
        ingest, leagues = _services(session)
        extractor = TournamentExtractor(
            ingest,
            leagues,
            football_data_token="fd",
        )
        matches = await extractor.process_matches(
            league_code="FAC",
            date_from=date(2026, 1, 10),
            date_to=date(2026, 1, 10),
        )
        await session.commit()

    assert len(matches) == 1
    assert matches[0].external_id == "fd-fac-1"

    with pytest.raises(ValueError, match="not a tournament"):
        await extractor.process_matches(
            league_code="PL",
            date_from=date(2026, 1, 10),
            date_to=date(2026, 1, 10),
        )
