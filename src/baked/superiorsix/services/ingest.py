"""Process-driven match and score ingest.

Extractors / workers call these helpers — they are not exposed as user REST APIs.
"""

from baked.superiorsix.domain.models import League, Match
from baked.superiorsix.services.leagues import LeagueService
from baked.superiorsix.services.matches import MatchService


class MatchIngestService:
    def __init__(self, matches: MatchService, leagues: LeagueService) -> None:
        self._matches = matches
        self._leagues = leagues

    async def upsert_league(self, league: League) -> League:
        return await self._leagues.upsert(league)

    async def upsert_match(self, match: Match) -> Match:
        return await self._matches.upsert_fixture(match)

    async def update_score(
        self,
        *,
        external_id: str | None = None,
        match_id: int | None = None,
        status: str,
        home_score: int | None = None,
        away_score: int | None = None,
        match_minute: int | None = None,
    ) -> Match:
        return await self._matches.apply_score_update(
            external_id=external_id,
            match_id=match_id,
            status=status,
            home_score=home_score,
            away_score=away_score,
            match_minute=match_minute,
        )
