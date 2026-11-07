"""Euros fixture / score ingest → ``euros_matches`` (not Super Six ``matches``)."""

from __future__ import annotations

import logging
from datetime import date

from baked.superiorsix.domain.models import EurosMatch, MatchStatus
from baked.superiorsix.process.extractors.connectors.base import JsonHttpClient
from baked.superiorsix.process.extractors.connectors.dto import MatchPayload
from baked.superiorsix.process.extractors.connectors.football_data import FootballDataConnector
from baked.superiorsix.services.euros import EurosService
from baked.superiorsix.services.leagues import LeagueService

logger = logging.getLogger(__name__)

EUROS_COMPETITION_CODE = "EC"


class EurosMatchExtractor:
    """Pull EC fixtures from football-data.org into isolated ``euros_matches``.

    Catalog (league row for ``EC``) still lives in shared ``leagues`` via the
    tournament catalog extractor. This path never writes Super Six ``matches``
    or World Cup tables.
    """

    def __init__(
        self,
        euros: EurosService,
        leagues: LeagueService,
        *,
        football_data_token: str | None,
        date_from: date,
        date_to: date,
        live: bool = False,
        http: JsonHttpClient | None = None,
    ) -> None:
        if date_to < date_from:
            raise ValueError("date_to must be on or after date_from.")
        self._euros = euros
        self._leagues = leagues
        self._date_from = date_from
        self._date_to = date_to
        self._live = live
        self._football_data = FootballDataConnector(
            football_data_token,
            http=http,
            league_codes=frozenset({EUROS_COMPETITION_CODE}),
        )

    async def process(self) -> list[EurosMatch]:
        league = await self._leagues.get_by_code(EUROS_COMPETITION_CODE)
        if league.id is None:
            raise RuntimeError(
                f"League '{EUROS_COMPETITION_CODE}' has no id — run tournament "
                "catalog ingest first."
            )
        payloads = self._football_data.collect_matches(
            EUROS_COMPETITION_CODE,
            date_from=self._date_from,
            date_to=self._date_to,
            season_start=league.start_date,
            live=self._live,
        )
        upserted: list[EurosMatch] = []
        for payload in payloads:
            match = await self._upsert(league.id, payload)
            upserted.append(match)
            logger.info(
                "euros match upserted %s vs %s status=%s matchday=%s et=%s pens=%s",
                match.home_team,
                match.away_team,
                match.status,
                match.matchday,
                match.extra_time,
                match.penalties,
            )
        return upserted

    async def _upsert(self, league_id: int, payload: MatchPayload) -> EurosMatch:
        matchday = payload.matchday if payload.matchday is not None else 1
        match = await self._euros.upsert_fixture(
            EurosMatch(
                external_id=payload.external_id,
                league_id=league_id,
                matchday=matchday,
                match_date=payload.match_date,
                status=payload.status,
                home_team=payload.home_team,
                away_team=payload.away_team,
                home_score=payload.home_score,
                away_score=payload.away_score,
                match_minute=payload.match_minute,
                extra_time=bool(payload.extra_time),
                penalties=bool(payload.penalties),
            )
        )
        if payload.status in {
            MatchStatus.IN_PLAY,
            MatchStatus.HALF_TIME,
            MatchStatus.FINISHED,
        } and (payload.home_score is not None or payload.away_score is not None):
            match = await self._euros.apply_score_update(
                external_id=payload.external_id,
                status=payload.status,
                home_score=payload.home_score,
                away_score=payload.away_score,
                match_minute=payload.match_minute,
                extra_time=payload.extra_time,
                penalties=payload.penalties,
                matchday=payload.matchday,
            )
        return match
