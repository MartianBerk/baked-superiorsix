from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from baked.superiorsix.domain.models import MatchStatus
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
    score_block = raw.get("score") or {}
    score = score_block.get("fullTime") or {}
    home_score = score.get("home")
    away_score = score.get("away")
    matchday_raw = raw.get("matchday")
    matchday = int(matchday_raw) if matchday_raw is not None else None
    extra_time, penalties = _parse_et_pens_flags(score_block)
    minute_raw = raw.get("minute")
    match_minute = int(minute_raw) if minute_raw is not None else None
    return MatchPayload(
        external_id=f"fd-{match_id}",
        match_date=match_date,
        status=map_football_data_status(raw.get("status")),
        home_team=home,
        away_team=away,
        home_score=int(home_score) if home_score is not None else None,
        away_score=int(away_score) if away_score is not None else None,
        match_minute=match_minute,
        matchday=matchday,
        extra_time=extra_time,
        penalties=penalties,
    )


def _parse_et_pens_flags(score_block: dict[str, Any]) -> tuple[bool | None, bool | None]:
    """Derive ET / pens booleans from football-data ``score`` (legacy Euros).

    A side having a non-null ET or pens value means that phase was played.
    Returns ``(None, None)`` when the feed omits both blocks (league fixtures).
    """
    et_block = score_block.get("extraTime")
    pens_block = score_block.get("penalties")
    if et_block is None and pens_block is None:
        return None, None

    def _phase_played(block: Any) -> bool:
        if not isinstance(block, dict):
            return False
        # v4 uses home/away; legacy connector used homeTeam/awayTeam.
        return any(
            block.get(key) is not None
            for key in ("home", "away", "homeTeam", "awayTeam")
        )

    return _phase_played(et_block or {}), _phase_played(pens_block or {})


def fold_side_game_full_time_scores(
    *,
    duration: str | None,
    full_time_home: int | None,
    full_time_away: int | None,
    penalties_home: int | None,
    penalties_away: int | None,
) -> tuple[int | None, int | None]:
    """Legacy WorldCupConnector fold for finished penalty shootouts.

    Strip penalty goals from fullTime, then add 1 to the shootout winner so
    home/away/draw predictions resolve against the knockout winner rather than
    the aggregated football-data fullTime totals.
    """
    if duration != "PENALTY_SHOOTOUT":
        return full_time_home, full_time_away
    if (
        full_time_home is None
        or full_time_away is None
        or penalties_home is None
        or penalties_away is None
    ):
        return full_time_home, full_time_away
    winner = "home" if full_time_home > full_time_away else "away"
    home = full_time_home - penalties_home
    away = full_time_away - penalties_away
    return home + (1 if winner == "home" else 0), away + (1 if winner == "away" else 0)


def parse_football_data_side_game_match(raw: dict[str, Any]) -> MatchPayload | None:
    """Parse a WC side-game fixture including matchday + ET/pens flags.

    Used by World Cup ingest only — Super Six / Euros match ingest keep
    :func:`parse_football_data_match` (Euros uses ET/pens from score blocks).
    """
    base = parse_football_data_match(raw)
    if base is None:
        return None
    score_block = raw.get("score") or {}
    duration = score_block.get("duration")
    status = map_football_data_status(raw.get("status"))
    extra_time: bool | None = None
    penalties: bool | None = None
    home_score = base.home_score
    away_score = base.away_score
    if status == MatchStatus.FINISHED:
        extra_time = duration == "EXTRA_TIME"
        penalties = duration == "PENALTY_SHOOTOUT"
        pens = score_block.get("penalties") or {}
        home_score, away_score = fold_side_game_full_time_scores(
            duration=duration,
            full_time_home=home_score,
            full_time_away=away_score,
            penalties_home=pens.get("home"),
            penalties_away=pens.get("away"),
        )
    matchday_raw = raw.get("matchday")
    matchday = int(matchday_raw) if matchday_raw is not None else None
    return MatchPayload(
        external_id=base.external_id,
        match_date=base.match_date,
        status=status,
        home_team=base.home_team,
        away_team=base.away_team,
        home_score=home_score,
        away_score=away_score,
        match_minute=base.match_minute,
        matchday=matchday,
        extra_time=extra_time,
        penalties=penalties,
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
        side_game: bool = False,
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
        parse = parse_football_data_side_game_match if side_game else parse_football_data_match
        matches: list[MatchPayload] = []
        for item in payload.get("matches") or []:
            parsed = parse(item)
            if parsed is not None:
                matches.append(parsed)
        return matches

