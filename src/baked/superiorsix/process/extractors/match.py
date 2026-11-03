from __future__ import annotations

import logging
from datetime import date

from baked.superiorsix.domain.models import Match, MatchStatus
from baked.superiorsix.process.extractors.connectors.base import JsonHttpClient
from baked.superiorsix.process.extractors.connectors.dto import MatchPayload
from baked.superiorsix.process.extractors.connectors.factory import connector_for_league
from baked.superiorsix.services.ingest import MatchIngestService
from baked.superiorsix.services.leagues import LeagueService

logger = logging.getLogger(__name__)


class MatchExtractor:
    """Pull fixtures for exactly one league code over an inclusive date range."""

    def __init__(
        self,
        ingest: MatchIngestService,
        leagues: LeagueService,
        *,
        league_code: str,
        date_from: date,
        date_to: date,
        football_data_token: str | None,
        goal_api_key: str | None,
        live: bool = False,
        http: JsonHttpClient | None = None,
    ) -> None:
        code = league_code.strip().upper()
        if not code:
            raise ValueError("league_code is required.")
        if date_to < date_from:
            raise ValueError("date_to must be on or after date_from.")
        self._ingest = ingest
        self._leagues = leagues
        self._league_code = code
        self._date_from = date_from
        self._date_to = date_to
        self._live = live
        self._connector = connector_for_league(
            code,
            football_data_token=football_data_token,
            goal_api_key=goal_api_key,
            http=http,
        )

    async def process(self) -> list[Match]:
        league = await self._leagues.get_by_code(self._league_code)
        if league.id is None:
            raise RuntimeError(f"League '{self._league_code}' has no id after load.")

        payloads = self._connector.collect_matches(
            self._league_code,
            date_from=self._date_from,
            date_to=self._date_to,
            season_start=league.start_date,
            live=self._live,
        )
        upserted: list[Match] = []
        for payload in payloads:
            match = await self._upsert(league.id, payload)
            upserted.append(match)
            window = (
                "live"
                if self._live
                else f"{self._date_from.isoformat()}..{self._date_to.isoformat()}"
            )
            logger.info(
                "match upserted league=%s %s %s vs %s status=%s",
                self._league_code,
                window,
                match.home_team,
                match.away_team,
                match.status,
            )
        return upserted

    async def _upsert(self, league_id: int, payload: MatchPayload) -> Match:
        match = await self._ingest.upsert_match(
            Match(
                external_id=payload.external_id,
                league_id=league_id,
                match_date=payload.match_date,
                status=payload.status,
                home_team=payload.home_team,
                away_team=payload.away_team,
                home_score=payload.home_score,
                away_score=payload.away_score,
                match_minute=payload.match_minute,
            )
        )
        # Apply scores when the feed already has them (no separate live poller here).
        if payload.status in {MatchStatus.IN_PLAY, MatchStatus.FINISHED} and (
            payload.home_score is not None or payload.away_score is not None
        ):
            match = await self._ingest.update_score(
                external_id=payload.external_id,
                status=payload.status,
                home_score=payload.home_score,
                away_score=payload.away_score,
                match_minute=payload.match_minute,
            )
        return match
