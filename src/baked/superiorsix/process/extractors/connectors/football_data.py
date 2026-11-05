from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from baked.superiorsix.process.extractors.connectors.base import JsonHttpClient
from baked.superiorsix.process.extractors.connectors.dto import LeaguePayload, MatchPayload
from baked.superiorsix.process.extractors.connectors.http import HttpxJsonClient
from baked.superiorsix.process.extractors.connectors.status import map_football_data_status

# England area id on football-data.org.
ENGLAND_AREA_ID = 2072
# World area — European Championship / World Cup live here, not under England.
WORLD_AREA_ID = 2267
# League catalog uses v2 so EL1/EL2 appear in the England competition list.
FOOTBALL_DATA_LEAGUES_BASE_URL = "https://api.football-data.org/v2"
# Match ingest for free-tier comps stays on v4.
FOOTBALL_DATA_BASE_URL = "https://api.football-data.org/v4"
# Superior Six English league codes.
LEAGUE_CATALOG_CODES: frozenset[str] = frozenset({"PL", "ELC", "EL1", "EL2"})
# Cup / international tournament codes (football-data free tier; not Flashscore).
# FAC comes from the England area list; EC / WC from the World area.
TOURNAMENT_CATALOG_CODES: frozenset[str] = frozenset({"FAC", "EC", "WC"})
ENGLAND_TOURNAMENT_CODES: frozenset[str] = frozenset({"FAC"})
WORLD_TOURNAMENT_CODES: frozenset[str] = frozenset({"EC", "WC"})
# Pseudo-status filter: IN_PLAY + PAUSED (see football-data match docs).
FOOTBALL_DATA_LIVE_STATUS = "LIVE"


def _parse_season_start_date(start_raw: str) -> date:
    """Accept v2 datetimes (``2017-08-11T19:00:00Z``) and v4 date-only values."""
    text = start_raw.strip()
    if "T" in text:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    return date.fromisoformat(text)


def parse_football_data_league(raw: dict[str, Any]) -> LeaguePayload | None:
    code = (raw.get("code") or "").strip()
    name = (raw.get("name") or "").strip()
    season = raw.get("currentSeason") or {}
    start_raw = season.get("startDate")
    if not code or not name or not start_raw:
        return None
    return LeaguePayload(
        name=name,
        code=code,
        start_date=_parse_season_start_date(str(start_raw)),
    )


def parse_football_data_match(raw: dict[str, Any]) -> MatchPayload | None:
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
    return MatchPayload(
        external_id=f"fd-{match_id}",
        match_date=match_date,
        status=map_football_data_status(raw.get("status")),
        home_team=home,
        away_team=away,
        home_score=int(home_score) if home_score is not None else None,
        away_score=int(away_score) if away_score is not None else None,
    )


class FootballDataConnector:
    """football-data.org connector for league catalog + free-tier fixtures.

    League catalog uses the v2 England competitions list (includes EL1/EL2).
    Match ingest uses v4 date ranges; EL1/EL2 fixtures are routed elsewhere.
    """

    provider = "football-data.org"

    def __init__(
        self,
        api_token: str | None,
        *,
        http: JsonHttpClient | None = None,
        base_url: str = FOOTBALL_DATA_BASE_URL,
        leagues_base_url: str = FOOTBALL_DATA_LEAGUES_BASE_URL,
        league_codes: frozenset[str] | None = None,
    ) -> None:
        self._token = (api_token or "").strip() or None
        self._http = http or HttpxJsonClient()
        self._base_url = base_url.rstrip("/")
        self._leagues_base_url = leagues_base_url.rstrip("/")
        self._league_codes = league_codes or LEAGUE_CATALOG_CODES

    def _headers(self) -> dict[str, str]:
        if not self._token:
            return {}
        return {"X-Auth-Token": self._token}

    def collect_leagues(self) -> list[LeaguePayload]:
        return self._collect_area_leagues(ENGLAND_AREA_ID, self._league_codes)

    def collect_tournaments(
        self,
        codes: frozenset[str] | None = None,
    ) -> list[LeaguePayload]:
        """Upsert cup / international tournament competitions.

        England cups (FAC) come from the England area list. EC / WC are loaded
        from the World area (legacy ``collect_euros`` / ``collect_world_cup``).
        """
        wanted = frozenset(code.strip().upper() for code in (codes or TOURNAMENT_CATALOG_CODES))
        if not wanted:
            return []
        england_codes = wanted & ENGLAND_TOURNAMENT_CODES
        world_codes = wanted & WORLD_TOURNAMENT_CODES
        unknown = wanted - ENGLAND_TOURNAMENT_CODES - WORLD_TOURNAMENT_CODES
        if unknown:
            raise ValueError(
                "Unsupported tournament codes: "
                + ", ".join(sorted(unknown))
                + f" (supported: {', '.join(sorted(TOURNAMENT_CATALOG_CODES))})"
            )
        leagues: list[LeaguePayload] = []
        seen: set[str] = set()
        if england_codes:
            for payload in self._collect_area_leagues(ENGLAND_AREA_ID, england_codes):
                if payload.code not in seen:
                    seen.add(payload.code)
                    leagues.append(payload)
        if world_codes:
            for payload in self._collect_area_leagues(WORLD_AREA_ID, world_codes):
                if payload.code not in seen:
                    seen.add(payload.code)
                    leagues.append(payload)
        return leagues

    def _collect_area_leagues(
        self,
        area_id: int,
        codes: frozenset[str],
    ) -> list[LeaguePayload]:
        payload = self._http.get_json(
            f"{self._leagues_base_url}/competitions",
            headers=self._headers() or None,
            params={"areas": area_id},
        )
        leagues: list[LeaguePayload] = []
        for item in payload.get("competitions") or []:
            parsed = parse_football_data_league(item)
            if parsed is None or parsed.code not in codes:
                continue
            leagues.append(parsed)
        return leagues

    def collect_matches(
        self,
        league_code: str,
        *,
        date_from: date,
        date_to: date,
        season_start: date | None = None,
        live: bool = False,
    ) -> list[MatchPayload]:
        del season_start
        if not self._token:
            raise RuntimeError("FOOTBALL_DATA_API_TOKEN is required for match ingest.")
        if live:
            params: dict[str, str] = {"status": FOOTBALL_DATA_LIVE_STATUS}
        else:
            if date_to < date_from:
                raise ValueError("date_to must be on or after date_from.")
            # football-data v4 treats dateTo as exclusive (end of previous UTC day).
            params = {
                "dateFrom": date_from.isoformat(),
                "dateTo": (date_to + timedelta(days=1)).isoformat(),
            }
        payload = self._http.get_json(
            f"{self._base_url}/competitions/{league_code.upper()}/matches",
            headers=self._headers(),
            params=params,
        )
        matches: list[MatchPayload] = []
        for item in payload.get("matches") or []:
            parsed = parse_football_data_match(item)
            if parsed is not None:
                matches.append(parsed)
        return matches
