from __future__ import annotations

import logging
from datetime import date

from baked.superiorsix.domain.models import League, Match
from baked.superiorsix.process.extractors.connectors.base import JsonHttpClient
from baked.superiorsix.process.extractors.connectors.dto import LeaguePayload
from baked.superiorsix.process.extractors.connectors.football_data import (
    TOURNAMENT_CATALOG_CODES,
    FootballDataConnector,
)
from baked.superiorsix.process.extractors.match import MatchExtractor
from baked.superiorsix.services.ingest import MatchIngestService
from baked.superiorsix.services.leagues import LeagueService

logger = logging.getLogger(__name__)


class TournamentExtractor:
    """Port of legacy euros/world-cup *league* catalog ingest — focused.

    Upserts football-data cup / international tournament rows (FAC, EC, WC) into
    the shared ``leagues`` table. Fixture pulls reuse :class:`MatchExtractor`
    (football-data path). Does not revive Flashscore / Selenium score agents or
    the side-game WorldCupMatch tables (SS-19 / SS-20).
    """

    def __init__(
        self,
        ingest: MatchIngestService,
        leagues: LeagueService,
        *,
        football_data_token: str | None,
        goal_api_key: str | None = None,
        codes: frozenset[str] | None = None,
        http: JsonHttpClient | None = None,
    ) -> None:
        self._ingest = ingest
        self._leagues = leagues
        self._football_data_token = football_data_token
        self._goal_api_key = goal_api_key
        self._codes = frozenset(
            code.strip().upper() for code in (codes or TOURNAMENT_CATALOG_CODES)
        )
        self._football_data = FootballDataConnector(
            football_data_token,
            http=http,
            league_codes=self._codes,
        )
        self._http = http

    async def process_catalog(self) -> list[League]:
        payloads = self._football_data.collect_tournaments(self._codes)
        upserted: list[League] = []
        seen: set[str] = set()
        for payload in payloads:
            if payload.code in seen:
                continue
            seen.add(payload.code)
            league = await self._upsert_league(payload)
            upserted.append(league)
            logger.info(
                "tournament league upserted code=%s name=%s start=%s",
                league.code,
                league.name,
                league.start_date,
            )
        missing = self._codes - {league.code for league in upserted}
        if missing:
            logger.warning(
                "tournament codes not returned by football-data: %s",
                ", ".join(sorted(missing)),
            )
        return upserted

    async def process_matches(
        self,
        *,
        league_code: str,
        date_from: date,
        date_to: date,
        live: bool = False,
    ) -> list[Match]:
        code = league_code.strip().upper()
        if code not in TOURNAMENT_CATALOG_CODES:
            raise ValueError(
                f"League '{code}' is not a tournament code "
                f"(supported: {', '.join(sorted(TOURNAMENT_CATALOG_CODES))})."
            )
        extractor = MatchExtractor(
            self._ingest,
            self._leagues,
            league_code=code,
            date_from=date_from,
            date_to=date_to,
            football_data_token=self._football_data_token,
            goal_api_key=self._goal_api_key,
            live=live,
            http=self._http,
        )
        return await extractor.process()

    async def _upsert_league(self, payload: LeaguePayload) -> League:
        return await self._ingest.upsert_league(
            League(
                name=payload.name,
                code=payload.code,
                start_date=payload.start_date,
            )
        )
