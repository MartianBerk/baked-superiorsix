from __future__ import annotations

from datetime import date, datetime
from typing import Any

from baked.superiorsix.process.extractors.connectors.base import JsonHttpClient
from baked.superiorsix.process.extractors.connectors.dto import LeaguePayload, MatchPayload
from baked.superiorsix.process.extractors.connectors.http import HttpxJsonClient
from baked.superiorsix.process.extractors.connectors.status import map_football_data_status

# England area id on football-data.org v4.
ENGLAND_AREA_ID = 2072
FOOTBALL_DATA_BASE_URL = "https://api.football-data.org/v4"


def parse_football_data_league(raw: dict[str, Any]) -> LeaguePayload | None:
    code = (raw.get("code") or "").strip()
    name = (raw.get("name") or "").strip()
    season = raw.get("currentSeason") or {}
    start_raw = season.get("startDate")
    if not code or not name or not start_raw:
        return None
    start_date = date.fromisoformat(start_raw)
    matchday = season.get("currentMatchday")
    return LeaguePayload(
        name=name,
        code=code,
        start_date=start_date,
        current_matchday=int(matchday) if matchday is not None else None,
    )


def parse_football_data_match(raw: dict[str, Any], *, match_week: int) -> MatchPayload | None:
    match_id = raw.get("id")
    if match_id is None:
        return None
    utc = raw.get("utcDate")
    if not utc:
        return None
    match_date = datetime.fromisoformat(utc.replace("Z", "+00:00"))
    home = (raw.get("homeTeam") or {}).get("name")
    away = (raw.get("awayTeam") or {}).get("name")
    if not home or not away:
        return None
    score = (raw.get("score") or {}).get("fullTime") or {}
    home_score = score.get("home")
    away_score = score.get("away")
    matchday = raw.get("matchday")
    return MatchPayload(
        external_id=f"fd-{match_id}",
        matchday=int(matchday) if matchday is not None else match_week,
        match_date=match_date,
        status=map_football_data_status(raw.get("status")),
        home_team=home,
        away_team=away,
        home_score=int(home_score) if home_score is not None else None,
        away_score=int(away_score) if away_score is not None else None,
    )


class FootballDataConnector:
    """football-data.org connector for free-tier competitions (PL, ELC, …)."""

    provider = "football-data.org"

    def __init__(
        self,
        api_token: str | None,
        *,
        http: JsonHttpClient | None = None,
        base_url: str = FOOTBALL_DATA_BASE_URL,
    ) -> None:
        self._token = (api_token or "").strip() or None
        self._http = http or HttpxJsonClient()
        self._base_url = base_url.rstrip("/")

    def _headers(self) -> dict[str, str]:
        if not self._token:
            return {}
        return {"X-Auth-Token": self._token}

    def collect_leagues(self) -> list[LeaguePayload]:
        payload = self._http.get_json(
            f"{self._base_url}/competitions",
            headers=self._headers() or None,
            params={"areas": ENGLAND_AREA_ID},
        )
        leagues: list[LeaguePayload] = []
        for item in payload.get("competitions") or []:
            parsed = parse_football_data_league(item)
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
        del season_start  # football-data scopes by competition code + matchday
        if not self._token:
            raise RuntimeError("FOOTBALL_DATA_API_TOKEN is required for match ingest.")
        payload = self._http.get_json(
            f"{self._base_url}/competitions/{league_code.upper()}/matches",
            headers=self._headers(),
            params={"matchday": match_week},
        )
        matches: list[MatchPayload] = []
        for item in payload.get("matches") or []:
            parsed = parse_football_data_match(item, match_week=match_week)
            if parsed is not None:
                matches.append(parsed)
        return matches
