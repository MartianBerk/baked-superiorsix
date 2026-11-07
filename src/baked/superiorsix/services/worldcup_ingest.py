"""Ingest helpers that write football-data fixtures into ``worldcup_*`` tables.

Does not touch Super Six ``matches`` or ``euros_*``.
"""

from __future__ import annotations

import logging

from baked.superiorsix.domain.match_status import coalesce_forward_status, is_status_regression
from baked.superiorsix.domain.models import League, MatchStatus
from baked.superiorsix.domain.worldcup import WorldcupMatch
from baked.superiorsix.repositories.worldcup_matches import WorldcupMatchRepository
from baked.superiorsix.services.leagues import LeagueService

logger = logging.getLogger(__name__)

WORLD_CUP_CODE = "WC"


class WorldcupIngestService:
    def __init__(
        self,
        matches: WorldcupMatchRepository,
        leagues: LeagueService,
    ) -> None:
        self._matches = matches
        self._leagues = leagues

    async def ensure_world_cup_league(self, league: League) -> League:
        if league.code.strip().upper() != WORLD_CUP_CODE:
            raise ValueError(f"Expected World Cup league code {WORLD_CUP_CODE}, got {league.code}")
        return await self._leagues.upsert(league)

    async def upsert_match(self, match: WorldcupMatch) -> WorldcupMatch:
        existing = await self._matches.get_by_external_id(match.external_id)
        if existing is None:
            return await self._matches.add(match)

        match.id = existing.id
        # Preserve admin deselection; ingest defaults use_match=True on create only.
        match.use_match = existing.use_match
        incoming_status = match.status
        if is_status_regression(existing.status, incoming_status):
            logger.warning(
                "rejecting regressive worldcup status external_id=%s current=%s incoming=%s",
                match.external_id,
                existing.status,
                incoming_status,
            )
            if match.home_score is None:
                match.home_score = existing.home_score
            if match.away_score is None:
                match.away_score = existing.away_score
            if match.match_minute is None:
                match.match_minute = existing.match_minute
            if not match.extra_time:
                match.extra_time = existing.extra_time
            if not match.penalties:
                match.penalties = existing.penalties
        match.status = coalesce_forward_status(existing.status, incoming_status)
        if match.matchday <= 0:
            match.matchday = existing.matchday
        return await self._matches.save(match)

    async def apply_score_update(
        self,
        *,
        external_id: str,
        status: str,
        home_score: int | None = None,
        away_score: int | None = None,
        match_minute: int | None = None,
        extra_time: bool | None = None,
        penalties: bool | None = None,
        matchday: int | None = None,
    ) -> WorldcupMatch:
        match = await self._matches.get_by_external_id(external_id)
        if match is None:
            raise LookupError(f"World Cup match external_id={external_id} not found.")
        if is_status_regression(match.status, status):
            logger.warning(
                "rejecting regressive worldcup score status external_id=%s "
                "current=%s incoming=%s",
                external_id,
                match.status,
                status,
            )
            status = match.status
        else:
            status = coalesce_forward_status(match.status, status)
        match.status = status
        if home_score is not None:
            match.home_score = home_score
        if away_score is not None:
            match.away_score = away_score
        if match_minute is not None:
            match.match_minute = match_minute
        elif status == MatchStatus.FINISHED and match.match_minute is None:
            match.match_minute = 90
        if extra_time is not None:
            match.extra_time = extra_time
        if penalties is not None:
            match.penalties = penalties
        if matchday is not None and matchday > 0:
            match.matchday = matchday
        return await self._matches.save(match)
