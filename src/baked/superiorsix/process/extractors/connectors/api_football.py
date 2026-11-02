from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from baked.superiorsix.process.extractors.connectors.base import JsonHttpClient
from baked.superiorsix.process.extractors.connectors.dto import LeaguePayload, MatchPayload
from baked.superiorsix.process.extractors.connectors.http import HttpxJsonClient
from baked.superiorsix.process.extractors.connectors.status import map_api_football_status

API_FOOTBALL_BASE_URL = "https://v3.football.api-sports.io"

# Internal league code → API-Football league id (England League One / Two).
API_FOOTBALL_LEAGUE_IDS: dict[str, int] = {
    "EL1": 40,
    "EL2": 41,
}

_ROUND_NUMBER = re.compile(r"(\d+)\s*$")


def season_year_from_start(season_start: date | None, fallback: date | None = None) -> int:
    """API-Football season parameter is the competition start year."""
    if season_start is not None:
        return season_start.year
    if fallback is not None:
        return fallback.year if fallback.month >= 8 else fallback.year - 1
    today = date.today()
    return today.year if today.month >= 8 else today.year - 1


def parse_round_matchday(round_name: str | None) -> int | None:
    if not round_name:
        return None
    match = _ROUND_NUMBER.search(round_name.strip())
    if match is None:
        return None
    return int(match.group(1))


def regular_season_round(match_week: int) -> str:
    return f"Regular Season - {match_week}"


def parse_api_football_league(
    raw: dict[str, Any],
    *,
    code: str,
    current_matchday: int | None = None,
) -> LeaguePayload | None:
    league = raw.get("league") or {}
    name = (league.get("name") or "").strip()
    seasons = raw.get("seasons") or []
    current = next((season for season in seasons if season.get("current")), None)
    if current is None and seasons:
        current = seasons[-1]
    if not name or current is None or not current.get("start"):
        return None
    start_date = date.fromisoformat(current["start"][:10])
    return LeaguePayload(
        name=name,
        code=code.upper(),
        start_date=start_date,
        current_matchday=current_matchday,
    )


def parse_api_football_fixture(
    raw: dict[str, Any],
    *,
    match_week: int,
) -> MatchPayload | None:
    fixture = raw.get("fixture") or {}
    fixture_id = fixture.get("id")
    if fixture_id is None:
        return None
    date_raw = fixture.get("date")
    if not date_raw:
        return None
    match_date = datetime.fromisoformat(date_raw.replace("Z", "+00:00"))
    teams = raw.get("teams") or {}
    home = ((teams.get("home") or {}).get("name") or "").strip()
    away = ((teams.get("away") or {}).get("name") or "").strip()
    if not home or not away:
        return None
    goals = raw.get("goals") or {}
    home_score = goals.get("home")
    away_score = goals.get("away")
    status_block = fixture.get("status") or {}
    elapsed = status_block.get("elapsed")
    league = raw.get("league") or {}
    round_matchday = parse_round_matchday(league.get("round"))
    return MatchPayload(
        external_id=f"af-{fixture_id}",
        matchday=round_matchday if round_matchday is not None else match_week,
        match_date=match_date,
        status=map_api_football_status(status_block.get("short")),
        home_team=home,
        away_team=away,
        home_score=int(home_score) if home_score is not None else None,
        away_score=int(away_score) if away_score is not None else None,
        match_minute=int(elapsed) if elapsed is not None else None,
    )


class ApiFootballConnector:
    """API-Football (API-Sports) connector for EL1 / EL2 only."""

    provider = "api-football"

    def __init__(
        self,
        api_key: str | None,
        *,
        http: JsonHttpClient | None = None,
        base_url: str = API_FOOTBALL_BASE_URL,
        league_ids: dict[str, int] | None = None,
    ) -> None:
        self._api_key = (api_key or "").strip() or None
        self._http = http or HttpxJsonClient()
        self._base_url = base_url.rstrip("/")
        self._league_ids = league_ids or dict(API_FOOTBALL_LEAGUE_IDS)

    @property
    def configured(self) -> bool:
        return self._api_key is not None

    def _headers(self) -> dict[str, str]:
        if not self._api_key:
            raise RuntimeError("API_FOOTBALL_KEY is required for API-Football ingest.")
        return {"x-apisports-key": self._api_key}

    def _league_id(self, league_code: str) -> int:
        try:
            return self._league_ids[league_code.upper()]
        except KeyError as exc:
            raise ValueError(
                f"League '{league_code}' is not mapped to API-Football "
                f"(supported: {', '.join(sorted(self._league_ids))})."
            ) from exc

    def collect_leagues(self) -> list[LeaguePayload]:
        leagues: list[LeaguePayload] = []
        for code, league_id in self._league_ids.items():
            payload = self._http.get_json(
                f"{self._base_url}/leagues",
                headers=self._headers(),
                params={"id": league_id},
            )
            rows = payload.get("response") or []
            if not rows:
                continue
            current_matchday = self._current_matchday(league_id, rows[0])
            parsed = parse_api_football_league(
                rows[0],
                code=code,
                current_matchday=current_matchday,
            )
            if parsed is not None:
                leagues.append(parsed)
        return leagues

    def collect_matches(
        self,
        league_code: str,
        match_week: int,
        *,
        season_start: date | None = None,
    ) -> list[MatchPayload]:
        league_id = self._league_id(league_code)
        season = season_year_from_start(season_start)
        payload = self._http.get_json(
            f"{self._base_url}/fixtures",
            headers=self._headers(),
            params={
                "league": league_id,
                "season": season,
                "round": regular_season_round(match_week),
            },
        )
        matches: list[MatchPayload] = []
        for item in payload.get("response") or []:
            parsed = parse_api_football_fixture(item, match_week=match_week)
            if parsed is not None:
                matches.append(parsed)
        return matches

    def _current_matchday(self, league_id: int, league_row: dict[str, Any]) -> int | None:
        seasons = league_row.get("seasons") or []
        current = next((season for season in seasons if season.get("current")), None)
        if current is None:
            return None
        season_year = current.get("year")
        if season_year is None:
            return None
        try:
            payload = self._http.get_json(
                f"{self._base_url}/fixtures/rounds",
                headers=self._headers(),
                params={"league": league_id, "season": season_year, "current": "true"},
            )
        except Exception:
            return None
        rounds = payload.get("response") or []
        if not rounds:
            return None
        return parse_round_matchday(str(rounds[0]))
