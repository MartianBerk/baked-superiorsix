from __future__ import annotations

import logging

from baked.superiorsix.domain.models import League
from baked.superiorsix.process.extractors.connectors.api_football import ApiFootballConnector
from baked.superiorsix.process.extractors.connectors.base import JsonHttpClient
from baked.superiorsix.process.extractors.connectors.dto import LeaguePayload
from baked.superiorsix.process.extractors.connectors.football_data import FootballDataConnector
from baked.superiorsix.services.ingest import MatchIngestService

logger = logging.getLogger(__name__)


class LeagueExtractor:
    """Refresh league catalog / current matchday from configured providers.

    - football-data.org: England free-tier competitions (PL, ELC, …)
    - API-Football: EL1 / EL2 only (not on football-data free tier)
    """

    def __init__(
        self,
        ingest: MatchIngestService,
        *,
        football_data_token: str | None,
        api_football_key: str | None,
        http: JsonHttpClient | None = None,
        include_api_football: bool = True,
    ) -> None:
        self._ingest = ingest
        self._football_data = FootballDataConnector(football_data_token, http=http)
        self._api_football = (
            ApiFootballConnector(api_football_key, http=http) if include_api_football else None
        )

    async def process(self) -> list[League]:
        payloads = self._football_data.collect_leagues()
        if self._api_football is not None and self._api_football.configured:
            payloads.extend(self._api_football.collect_leagues())
        elif self._api_football is not None:
            logger.warning("API_FOOTBALL_KEY unset; skipping EL1/EL2 league catalog pull.")

        upserted: list[League] = []
        seen: set[str] = set()
        for payload in payloads:
            if payload.code in seen:
                continue
            seen.add(payload.code)
            league = await self._upsert(payload)
            upserted.append(league)
            logger.info(
                "league upserted code=%s name=%s matchday=%s",
                league.code,
                league.name,
                league.current_matchday,
            )
        return upserted

    async def _upsert(self, payload: LeaguePayload) -> League:
        return await self._ingest.upsert_league(
            League(
                name=payload.name,
                code=payload.code,
                start_date=payload.start_date,
                current_matchday=payload.current_matchday,
            )
        )
