from __future__ import annotations

import logging

from baked.superiorsix.domain.models import League
from baked.superiorsix.process.extractors.connectors.base import JsonHttpClient
from baked.superiorsix.process.extractors.connectors.dto import LeaguePayload
from baked.superiorsix.process.extractors.connectors.football_data import FootballDataConnector
from baked.superiorsix.services.ingest import MatchIngestService

logger = logging.getLogger(__name__)


class LeagueExtractor:
    """Refresh league catalog from football-data.org v2.

    Match ingest is date-ranged. EL1/EL2 fixtures use Goal API via the match
    extractor.
    """

    def __init__(
        self,
        ingest: MatchIngestService,
        *,
        football_data_token: str | None,
        http: JsonHttpClient | None = None,
    ) -> None:
        self._ingest = ingest
        self._football_data = FootballDataConnector(football_data_token, http=http)

    async def process(self) -> list[League]:
        payloads = self._football_data.collect_leagues()
        upserted: list[League] = []
        seen: set[str] = set()
        for payload in payloads:
            if payload.code in seen:
                continue
            seen.add(payload.code)
            league = await self._upsert(payload)
            upserted.append(league)
            logger.info(
                "league upserted code=%s name=%s start=%s",
                league.code,
                league.name,
                league.start_date,
            )
        return upserted

    async def _upsert(self, payload: LeaguePayload) -> League:
        return await self._ingest.upsert_league(
            League(
                name=payload.name,
                code=payload.code,
                start_date=payload.start_date,
            )
        )
