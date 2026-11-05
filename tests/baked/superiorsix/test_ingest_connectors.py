from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pytest

from baked.superiorsix.domain.models import MatchStatus
from baked.superiorsix.process.extractors.connectors.factory import (
    GOAL_API_LEAGUE_CODES,
    connector_for_league,
    provider_for_league,
)
from baked.superiorsix.process.extractors.connectors.football_data import (
    FootballDataConnector,
    parse_football_data_league,
    parse_football_data_match,
)
from baked.superiorsix.process.extractors.connectors.goal_api import (
    GoalApiConnector,
    parse_goal_api_fixture,
    raise_for_goal_api_errors,
)
from baked.superiorsix.process.extractors.connectors.status import (
    map_football_data_status,
    map_goal_api_status,
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


def test_factory_routes_el1_el2_to_goal_api() -> None:
    assert provider_for_league("EL1") == "goal-api"
    assert provider_for_league("el2") == "goal-api"
    assert GOAL_API_LEAGUE_CODES == frozenset({"EL1", "EL2"})
    connector = connector_for_league(
        "EL1",
        football_data_token="fd",
        goal_api_key="goal",
    )
    assert isinstance(connector, GoalApiConnector)


def test_factory_routes_other_codes_to_football_data() -> None:
    assert provider_for_league("PL") == "football-data.org"
    assert provider_for_league("ELC") == "football-data.org"
    connector = connector_for_league(
        "PL",
        football_data_token="fd",
        goal_api_key="goal",
    )
    assert isinstance(connector, FootballDataConnector)


def test_status_mapping() -> None:
    assert map_football_data_status("TIMED") == MatchStatus.SCHEDULED
    assert map_football_data_status("IN_PLAY") == MatchStatus.IN_PLAY
    assert map_football_data_status("PAUSED") == MatchStatus.HALF_TIME
    assert map_football_data_status("FINISHED") == MatchStatus.FINISHED
    assert map_goal_api_status("SCHEDULED") == MatchStatus.SCHEDULED
    assert map_goal_api_status("LIVE") == MatchStatus.IN_PLAY
    assert map_goal_api_status("HALF_TIME") == MatchStatus.HALF_TIME
    assert map_goal_api_status("FINISHED") == MatchStatus.FINISHED
    assert map_goal_api_status("POSTPONED") == MatchStatus.POSTPONED


def test_parse_football_data_league_and_match() -> None:
    league = parse_football_data_league(
        {
            "name": "Premier League",
            "code": "PL",
            "currentSeason": {"startDate": "2025-08-15", "currentMatchday": 99},
        }
    )
    assert league is not None
    assert league.code == "PL"
    assert league.start_date == date(2025, 8, 15)

    match = parse_football_data_match(
        {
            "id": 123,
            "utcDate": "2025-10-18T14:00:00Z",
            "status": "TIMED",
            "homeTeam": {"name": "Arsenal"},
            "awayTeam": {"name": "Chelsea"},
            "score": {"fullTime": {"home": None, "away": None}},
        }
    )
    assert match is not None
    assert match.external_id == "fd-123"
    assert match.status == MatchStatus.SCHEDULED
    assert match.match_date == datetime(2025, 10, 18, 14, 0, tzinfo=UTC)


def test_parse_goal_api_fixture() -> None:
    assert GoalApiConnector("key", http=FakeHttp({})).collect_leagues() == []
    match = parse_goal_api_fixture(
        {
            "id": "cuid-1",
            "apiId": "999",
            "kickoffUtc": "2025-10-18T14:00:00.000Z",
            "matchStatus": "FINISHED",
            "matchElapsed": 90,
            "homeTeamName": "Barnsley",
            "awayTeamName": "Bolton",
            "homeTeamScore": "2",
            "awayTeamScore": "1",
        }
    )
    assert match is not None
    assert match.external_id == "ga-999"
    assert match.status == MatchStatus.FINISHED
    assert match.home_score == 2
    assert match.away_score == 1
    assert match.match_minute == 90


def test_goal_api_raises_on_error_envelope() -> None:
    with pytest.raises(RuntimeError, match="Authentication required"):
        raise_for_goal_api_errors(
            {"success": False, "message": "Authentication required"},
            context="fixtures",
        )


def test_football_data_collect_leagues_uses_v2_catalogue() -> None:
    http = FakeHttp(
        {
            (
                "https://api.football-data.org/v2/competitions",
                (("areas", 2072),),
            ): {
                "competitions": [
                    {
                        "name": "Premier League",
                        "code": "PL",
                        "currentSeason": {
                            "startDate": "2026-08-21T00:00:00Z",
                            "currentMatchday": 1,
                        },
                    },
                    {
                        "name": "League One",
                        "code": "EL1",
                        "currentSeason": {
                            "startDate": "2026-08-15T00:00:00Z",
                            "currentMatchday": 1,
                        },
                    },
                    {
                        "name": "National League",
                        "code": "ENL",
                        "currentSeason": {
                            "startDate": "2026-08-08T00:00:00Z",
                            "currentMatchday": 1,
                        },
                    },
                ]
            }
        }
    )
    connector = FootballDataConnector("token", http=http)
    leagues = {league.code: league for league in connector.collect_leagues()}
    assert set(leagues) == {"PL", "EL1"}
    assert leagues["EL1"].name == "League One"


def test_football_data_collect_matches_uses_inclusive_date_range() -> None:
    http = FakeHttp(
        {
            (
                "https://api.football-data.org/v4/competitions/PL/matches",
                (("dateFrom", "2025-11-01"), ("dateTo", "2025-11-04")),
            ): {
                "matches": [
                    {
                        "id": 55,
                        "utcDate": "2025-11-01T15:00:00Z",
                        "status": "SCHEDULED",
                        "homeTeam": {"name": "Liverpool"},
                        "awayTeam": {"name": "Everton"},
                        "score": {"fullTime": {"home": None, "away": None}},
                    }
                ]
            }
        }
    )
    connector = FootballDataConnector("token", http=http)
    matches = connector.collect_matches(
        "PL",
        date_from=date(2025, 11, 1),
        date_to=date(2025, 11, 3),
    )
    assert len(matches) == 1
    assert matches[0].external_id == "fd-55"


def test_football_data_live_filters_status_live() -> None:
    http = FakeHttp(
        {
            (
                "https://api.football-data.org/v4/competitions/PL/matches",
                (("status", "LIVE"),),
            ): {
                "matches": [
                    {
                        "id": 99,
                        "utcDate": "2026-11-03T15:00:00Z",
                        "status": "IN_PLAY",
                        "homeTeam": {"name": "Arsenal"},
                        "awayTeam": {"name": "Chelsea"},
                        "score": {"fullTime": {"home": 1, "away": 0}},
                    }
                ]
            }
        }
    )
    connector = FootballDataConnector("token", http=http)
    matches = connector.collect_matches(
        "PL",
        date_from=date(2026, 1, 1),
        date_to=date(2026, 1, 31),
        live=True,
    )
    assert len(matches) == 1
    assert matches[0].external_id == "fd-99"
    assert matches[0].status == MatchStatus.IN_PLAY
    assert http.calls == [
        (
            "https://api.football-data.org/v4/competitions/PL/matches",
            {"status": "LIVE"},
        )
    ]


def test_goal_api_collect_matches_paginates_each_date() -> None:
    el1 = "cmr77dvkr005lrx061tywzotk"
    http = FakeHttp(
        {
            (
                "https://api.goal-api.com/v1/fixtures/date/2025-09-20",
                (("leagueId", el1), ("limit", 100), ("offset", 0)),
            ): {
                "success": True,
                "data": [
                    {
                        "apiId": "1",
                        "leagueId": el1,
                        "kickoffUtc": "2025-09-20T14:00:00.000Z",
                        "matchStatus": "SCHEDULED",
                        "homeTeamName": "Wrexham",
                        "awayTeamName": "Reading",
                    }
                ],
                "pagination": {"total": 2, "limit": 100, "offset": 0, "hasMore": True},
            },
            (
                "https://api.goal-api.com/v1/fixtures/date/2025-09-20",
                (("leagueId", el1), ("limit", 100), ("offset", 100)),
            ): {
                "success": True,
                "data": [
                    {
                        "apiId": "2",
                        "leagueId": el1,
                        "kickoffUtc": "2025-09-20T16:00:00.000Z",
                        "matchStatus": "SCHEDULED",
                        "homeTeamName": "Barnsley",
                        "awayTeamName": "Bolton",
                    }
                ],
                "pagination": {"total": 2, "limit": 100, "offset": 100, "hasMore": False},
            },
            (
                "https://api.goal-api.com/v1/fixtures/date/2025-09-21",
                (("leagueId", el1), ("limit", 100), ("offset", 0)),
            ): {
                "success": True,
                "data": [],
                "pagination": {"total": 0, "limit": 100, "offset": 0, "hasMore": False},
            },
        }
    )
    connector = GoalApiConnector("key", http=http)
    matches = connector.collect_matches(
        "EL1",
        date_from=date(2025, 9, 20),
        date_to=date(2025, 9, 21),
    )
    assert [match.external_id for match in matches] == ["ga-1", "ga-2"]
    assert len(http.calls) == 3


def test_goal_api_collect_matches_live() -> None:
    el1 = "cmr77dvkr005lrx061tywzotk"
    http = FakeHttp(
        {
            (
                "https://api.goal-api.com/v1/fixtures/live",
                (("leagueId", el1), ("limit", 100), ("offset", 0)),
            ): {
                "success": True,
                "data": [
                    {
                        "apiId": "77",
                        "leagueId": el1,
                        "kickoffUtc": "2025-09-20T14:00:00.000Z",
                        "matchStatus": "LIVE",
                        "matchElapsed": 67,
                        "homeTeamName": "Wrexham",
                        "awayTeamName": "Reading",
                        "homeTeamScore": "1",
                        "awayTeamScore": "0",
                    }
                ],
                "pagination": {"total": 1, "limit": 100, "offset": 0, "hasMore": False},
            }
        }
    )
    connector = GoalApiConnector("key", http=http)
    matches = connector.collect_matches(
        "EL1",
        date_from=date(2025, 1, 1),
        date_to=date(2025, 1, 2),
        live=True,
    )
    assert len(matches) == 1
    assert matches[0].status == MatchStatus.IN_PLAY
    assert matches[0].match_minute == 67
