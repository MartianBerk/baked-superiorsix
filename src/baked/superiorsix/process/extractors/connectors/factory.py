from __future__ import annotations

from baked.superiorsix.process.extractors.connectors.base import (
    JsonHttpClient,
    LeagueMatchConnector,
)
from baked.superiorsix.process.extractors.connectors.football_data import FootballDataConnector
from baked.superiorsix.process.extractors.connectors.goal_api import (
    GOAL_API_LEAGUE_IDS,
    GoalApiConnector,
)

# Match-ingest league codes routed to Goal API. League catalog is football-data.org v2.
GOAL_API_LEAGUE_CODES: frozenset[str] = frozenset(GOAL_API_LEAGUE_IDS)


def provider_for_league(league_code: str) -> str:
    code = league_code.strip().upper()
    if code in GOAL_API_LEAGUE_CODES:
        return "goal-api"
    return "football-data.org"


def connector_for_league(
    league_code: str,
    *,
    football_data_token: str | None,
    goal_api_key: str | None,
    http: JsonHttpClient | None = None,
) -> LeagueMatchConnector:
    """Select the ingest connector from the Superior Six league code."""
    code = league_code.strip().upper()
    if not code:
        raise ValueError("league_code is required.")
    if provider_for_league(code) == "goal-api":
        return GoalApiConnector(goal_api_key, http=http)
    return FootballDataConnector(football_data_token, http=http)
