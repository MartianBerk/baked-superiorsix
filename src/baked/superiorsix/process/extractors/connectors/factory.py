from __future__ import annotations

from baked.superiorsix.process.extractors.connectors.api_football import (
    API_FOOTBALL_LEAGUE_IDS,
    ApiFootballConnector,
)
from baked.superiorsix.process.extractors.connectors.base import (
    JsonHttpClient,
    LeagueMatchConnector,
)
from baked.superiorsix.process.extractors.connectors.football_data import FootballDataConnector

# League codes routed to API-Football (API-Sports). Everything else → football-data.org.
API_FOOTBALL_LEAGUE_CODES: frozenset[str] = frozenset(API_FOOTBALL_LEAGUE_IDS)


def provider_for_league(league_code: str) -> str:
    code = league_code.strip().upper()
    if code in API_FOOTBALL_LEAGUE_CODES:
        return "api-football"
    return "football-data.org"


def connector_for_league(
    league_code: str,
    *,
    football_data_token: str | None,
    api_football_key: str | None,
    http: JsonHttpClient | None = None,
) -> LeagueMatchConnector:
    """Select the ingest connector from the Superior Six league code."""
    code = league_code.strip().upper()
    if not code:
        raise ValueError("league_code is required.")
    if provider_for_league(code) == "api-football":
        return ApiFootballConnector(api_football_key, http=http)
    return FootballDataConnector(football_data_token, http=http)
