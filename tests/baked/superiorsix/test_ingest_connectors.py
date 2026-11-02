from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pytest

from baked.superiorsix.domain.models import MatchStatus
from baked.superiorsix.process.extractors.connectors.api_football import (
    ApiFootballConnector,
    parse_api_football_fixture,
    parse_api_football_league,
    parse_round_matchday,
    regular_season_round,
    season_year_from_start,
)
from baked.superiorsix.process.extractors.connectors.factory import (
    API_FOOTBALL_LEAGUE_CODES,
    connector_for_league,
    provider_for_league,
)
from baked.superiorsix.process.extractors.connectors.football_data import (
    FootballDataConnector,
    parse_football_data_league,
    parse_football_data_match,
)
from baked.superiorsix.process.extractors.connectors.status import (
    map_api_football_status,
    map_football_data_status,
)


class FakeHttp:
    def __init__(self, routes: dict[tuple[str, tuple[tuple[str, Any], ...]], Any]) -> None:
        self._routes = routes
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    def get_json(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        del headers
        key = (url, tuple(sorted((params or {}).items())))
        self.calls.append((url, params))
        if key not in self._routes:
            raise AssertionError(f"unexpected request {key!r}; known={list(self._routes)}")
        return self._routes[key]


def test_factory_routes_el1_el2_to_api_football() -> None:
    assert provider_for_league("EL1") == "api-football"
    assert provider_for_league("el2") == "api-football"
    assert API_FOOTBALL_LEAGUE_CODES == frozenset({"EL1", "EL2"})
    connector = connector_for_league(
        "EL1",
        football_data_token="fd",
        api_football_key="af",
    )
    assert isinstance(connector, ApiFootballConnector)


def test_factory_routes_other_codes_to_football_data() -> None:
    assert provider_for_league("PL") == "football-data.org"
    assert provider_for_league("ELC") == "football-data.org"
    connector = connector_for_league(
        "PL",
        football_data_token="fd",
        api_football_key="af",
    )
    assert isinstance(connector, FootballDataConnector)


def test_status_mapping() -> None:
    assert map_football_data_status("TIMED") == MatchStatus.SCHEDULED
    assert map_football_data_status("IN_PLAY") == MatchStatus.IN_PLAY
    assert map_football_data_status("FINISHED") == MatchStatus.FINISHED
    assert map_api_football_status("NS") == MatchStatus.SCHEDULED
    assert map_api_football_status("2H") == MatchStatus.IN_PLAY
    assert map_api_football_status("FT") == MatchStatus.FINISHED
    assert map_api_football_status("PST") == MatchStatus.POSTPONED


def test_parse_football_data_league_and_match() -> None:
    league = parse_football_data_league(
        {
            "name": "Premier League",
            "code": "PL",
            "currentSeason": {"startDate": "2025-08-15", "currentMatchday": 11},
        }
    )
    assert league is not None
    assert league.code == "PL"
    assert league.start_date == date(2025, 8, 15)
    assert league.current_matchday == 11

    match = parse_football_data_match(
        {
            "id": 123,
            "utcDate": "2025-10-18T14:00:00Z",
            "status": "TIMED",
            "matchday": 8,
            "homeTeam": {"name": "Arsenal"},
            "awayTeam": {"name": "Chelsea"},
            "score": {"fullTime": {"home": None, "away": None}},
        },
        match_week=8,
    )
    assert match is not None
    assert match.external_id == "fd-123"
    assert match.status == MatchStatus.SCHEDULED
    assert match.match_date == datetime(2025, 10, 18, 14, 0, tzinfo=UTC)


def test_parse_api_football_helpers() -> None:
    assert regular_season_round(10) == "Regular Season - 10"
    assert parse_round_matchday("Regular Season - 12") == 12
    assert season_year_from_start(date(2025, 8, 1)) == 2025

    league = parse_api_football_league(
        {
            "league": {"id": 40, "name": "League One"},
            "seasons": [
                {"year": 2025, "start": "2025-08-01", "end": "2026-05-01", "current": True},
            ],
        },
        code="EL1",
        current_matchday=5,
    )
    assert league is not None
    assert league.code == "EL1"
    assert league.current_matchday == 5

    match = parse_api_football_fixture(
        {
            "fixture": {
                "id": 999,
                "date": "2025-10-18T14:00:00+00:00",
                "status": {"short": "FT", "elapsed": 90},
            },
            "league": {"round": "Regular Season - 12"},
            "teams": {
                "home": {"name": "Barnsley"},
                "away": {"name": "Bolton"},
            },
            "goals": {"home": 2, "away": 1},
        },
        match_week=12,
    )
    assert match is not None
    assert match.external_id == "af-999"
    assert match.status == MatchStatus.FINISHED
    assert match.home_score == 2
    assert match.away_score == 1
    assert match.matchday == 12


def test_football_data_collect_matches_requires_token() -> None:
    connector = FootballDataConnector(None, http=FakeHttp({}))
    with pytest.raises(RuntimeError, match="FOOTBALL_DATA_API_TOKEN"):
        connector.collect_matches("PL", 1)


def test_football_data_collect_matches_parses_http() -> None:
    http = FakeHttp(
        {
            (
                "https://api.football-data.org/v4/competitions/PL/matches",
                (("matchday", 10),),
            ): {
                "matches": [
                    {
                        "id": 55,
                        "utcDate": "2025-11-01T15:00:00Z",
                        "status": "SCHEDULED",
                        "matchday": 10,
                        "homeTeam": {"name": "Liverpool"},
                        "awayTeam": {"name": "Everton"},
                        "score": {"fullTime": {"home": None, "away": None}},
                    }
                ]
            }
        }
    )
    connector = FootballDataConnector("token", http=http)
    matches = connector.collect_matches("PL", 10)
    assert len(matches) == 1
    assert matches[0].external_id == "fd-55"
    assert matches[0].home_team == "Liverpool"


def test_api_football_collect_matches_parses_http() -> None:
    http = FakeHttp(
        {
            (
                "https://v3.football.api-sports.io/fixtures",
                (
                    ("league", 40),
                    ("round", "Regular Season - 7"),
                    ("season", 2025),
                ),
            ): {
                "response": [
                    {
                        "fixture": {
                            "id": 42,
                            "date": "2025-09-20T14:00:00+00:00",
                            "status": {"short": "NS", "elapsed": None},
                        },
                        "league": {"round": "Regular Season - 7"},
                        "teams": {
                            "home": {"name": "Wrexham"},
                            "away": {"name": "Reading"},
                        },
                        "goals": {"home": None, "away": None},
                    }
                ]
            }
        }
    )
    connector = ApiFootballConnector("key", http=http)
    matches = connector.collect_matches("EL1", 7, season_start=date(2025, 8, 1))
    assert len(matches) == 1
    assert matches[0].external_id == "af-42"
    assert matches[0].away_team == "Reading"
