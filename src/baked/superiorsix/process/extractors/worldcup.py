"""World Cup side-game extractor — writes ``worldcup_*`` tables only.

Catalog still upserts the WC row into shared ``leagues`` (legacy behaviour).
Fixtures and scores go to ``worldcup_matches``, never Super Six ``matches``.
"""

from __future__ import annotations

import logging
from datetime import date

from baked.superiorsix.domain.exceptions import LeagueNotFoundError
from baked.superiorsix.domain.models import League
from baked.superiorsix.domain.worldcup import WorldcupMatch
from baked.superiorsix.process.extractors.connectors.base import JsonHttpClient
from baked.superiorsix.process.extractors.connectors.dto import MatchPayload
from baked.superiorsix.process.extractors.connectors.football_data import (
    WORLD_TOURNAMENT_CODES,
    FootballDataConnector,
)
from baked.superiorsix.services.leagues import LeagueService
from baked.superiorsix.services.worldcup_ingest import WORLD_CUP_CODE, WorldcupIngestService

logger = logging.getLogger(__name__)


class WorldcupExtractor:
    """Port of legacy ``WorldCupExtractor`` for the modern stack."""

    def __init__(
        self,
        ingest: WorldcupIngestService,
        leagues: LeagueService,
        *,
        football_data_token: str | None,
        http: JsonHttpClient | None = None,
        matchday_override: int | None = None,
    ) -> None:
        self._ingest = ingest
        self._leagues = leagues
        self._matchday_override = matchday_override
        self._football_data = FootballDataConnector(
            football_data_token,
            http=http,
            league_codes=WORLD_TOURNAMENT_CODES,
        )

    async def process_catalog(self) -> League | None:
        payloads = self._football_data.collect_tournaments(frozenset({WORLD_CUP_CODE}))
        if not payloads:
            logger.warning("World Cup competition not returned by football-data")
            return None
        payload = payloads[0]
        league = await self._ingest.ensure_world_cup_league(
            League(name=payload.name, code=payload.code, start_date=payload.start_date)
        )
        logger.info(
            "worldcup league upserted code=%s name=%s start=%s",
            league.code,
            league.name,
            league.start_date,
        )
        return league

    async def process_matches(
        self,
        *,
        date_from: date,
        date_to: date,
        live: bool = False,
    ) -> list[WorldcupMatch]:
        try:
            league = await self._leagues.get_by_code(WORLD_CUP_CODE)
        except LeagueNotFoundError as exc:
            raise ValueError(
                f"League '{WORLD_CUP_CODE}' is not in the catalog; "
                "run worldcup catalog ingest first."
            ) from exc
        assert league.id is not None
        raw_matches = self._football_data.collect_matches(
            WORLD_CUP_CODE,
            date_from=date_from,
            date_to=date_to,
            live=live,
            side_game=True,
        )
        upserted: list[WorldcupMatch] = []
        for payload in raw_matches:
            matchday = self._resolve_matchday(payload)
            upserted.append(
                await self._ingest.upsert_match(
                    WorldcupMatch(
                        external_id=payload.external_id,
                        league_id=league.id,
                        matchday=matchday,
                        match_date=payload.match_date,
                        status=payload.status,
                        home_team=payload.home_team,
                        away_team=payload.away_team,
                        match_minute=payload.match_minute,
                        home_score=payload.home_score,
                        away_score=payload.away_score,
                        extra_time=bool(payload.extra_time),
                        penalties=bool(payload.penalties),
                        use_match=True,
                    )
                )
            )
        logger.info(
            "worldcup matches upserted live=%s from=%s to=%s count=%s",
            live,
            date_from.isoformat(),
            date_to.isoformat(),
            len(upserted),
        )
        return upserted

    def _resolve_matchday(self, payload: MatchPayload) -> int:
        if self._matchday_override is not None:
            return self._matchday_override
        if payload.matchday is not None and payload.matchday > 0:
            return payload.matchday
        # Legacy LAST_32 quirk: matchday can be null — default group-stage floor.
        return 1
