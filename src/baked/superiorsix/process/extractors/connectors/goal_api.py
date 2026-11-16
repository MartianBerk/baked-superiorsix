from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime, timedelta
from typing import Any

from baked.superiorsix.process.extractors.connectors.base import JsonHttpClient
from baked.superiorsix.process.extractors.connectors.dto import LeaguePayload, MatchPayload
from baked.superiorsix.process.extractors.connectors.http import HttpxJsonClient
from baked.superiorsix.process.extractors.connectors.status import map_goal_api_status

GOAL_API_BASE_URL = "https://api.goal-api.com/v1"
DEFAULT_PAGE_LIMIT = 100

# Internal league code → Goal API league id (England League One / Two).
# IDs from https://goal-api.com/coverage (England League One / League Two).
GOAL_API_LEAGUE_IDS: dict[str, str] = {
    "EL1": "cmr77dvkr005lrx061tywzotk",
    "EL2": "cmr77dvkr005mrx06iayzvq2w",
}


def _team_name(raw: dict[str, Any], *, side: str) -> str:
    flat = (raw.get(f"{side}TeamName") or "").strip()
    if flat:
        return flat
    nested = raw.get(f"{side}Team") or {}
    return (nested.get("name") or "").strip()


def _parse_score(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(str(value).strip())
    except ValueError:
        return None


def _parse_minute(raw: dict[str, Any]) -> int | None:
    elapsed = raw.get("matchElapsed")
    if elapsed is not None:
        try:
            return int(elapsed)
        except (TypeError, ValueError):
            pass
    minute = raw.get("matchMinute")
    if minute is None or minute == "":
        return None
    text = str(minute).strip()
    digits = "".join(ch for ch in text if ch.isdigit())
    if not digits:
        return None
    try:
        return int(digits)
    except ValueError:
        return None


def parse_goal_api_fixture(raw: dict[str, Any]) -> MatchPayload | None:
    fixture_id = raw.get("apiId") or raw.get("id")
    if fixture_id is None:
        return None
    kickoff = raw.get("kickoffUtc") or raw.get("matchDate")
    if not kickoff:
        return None
    kickoff_text = str(kickoff)
    if "T" in kickoff_text:
        match_date = datetime.fromisoformat(kickoff_text.replace("Z", "+00:00"))
    else:
        match_date = datetime.fromisoformat(f"{kickoff_text}T00:00:00+00:00")
    home = _team_name(raw, side="home")
    away = _team_name(raw, side="away")
    if not home or not away:
        return None
    return MatchPayload(
        external_id=f"ga-{fixture_id}",
        match_date=match_date,
        status=map_goal_api_status(raw.get("matchStatus")),
        home_team=home,
        away_team=away,
        home_score=_parse_score(raw.get("homeTeamScore")),
        away_score=_parse_score(raw.get("awayTeamScore")),
        match_minute=_parse_minute(raw),
    )


def raise_for_goal_api_errors(payload: dict[str, Any], *, context: str) -> None:
    if payload.get("success") is False:
        message = payload.get("message") or payload.get("code") or "unknown error"
        raise RuntimeError(f"Goal API {context} failed ({message})")


class GoalApiConnector:
    """Goal API connector for EL1 / EL2 fixture ingest.

    Date-range pulls call ``/fixtures/date/:date`` once per calendar day and
    paginate with ``limit`` / ``offset``. ``live=True`` uses ``/fixtures/live``.
    """

    provider = "goal-api"

    def __init__(
        self,
        api_key: str | None,
        *,
        http: JsonHttpClient | None = None,
        base_url: str = GOAL_API_BASE_URL,
        league_ids: dict[str, str] | None = None,
        page_limit: int = DEFAULT_PAGE_LIMIT,
    ) -> None:
        self._api_key = (api_key or "").strip() or None
        self._http = http or HttpxJsonClient()
        self._base_url = base_url.rstrip("/")
        self._league_ids = league_ids or dict(GOAL_API_LEAGUE_IDS)
        self._page_limit = min(max(page_limit, 1), 500)

    @property
    def configured(self) -> bool:
        return self._api_key is not None

    def _headers(self) -> dict[str, str]:
        if not self._api_key:
            raise RuntimeError("GOAL_API_KEY is required for Goal API ingest.")
        return {"Authorization": f"Bearer {self._api_key}"}

    def _league_id(self, league_code: str) -> str:
        try:
            return self._league_ids[league_code.upper()]
        except KeyError as exc:
            raise ValueError(
                f"League '{league_code}' is not mapped to Goal API "
                f"(supported: {', '.join(sorted(self._league_ids))})."
            ) from exc

    def collect_leagues(self) -> list[LeaguePayload]:
        # League catalog is owned by football-data.org (LeagueExtractor).
        return []

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
        if live:
            return self._collect_live(league_code)
        if date_to < date_from:
            raise ValueError("date_to must be on or after date_from.")
        matches: list[MatchPayload] = []
        day = date_from
        while day <= date_to:
            matches.extend(self._collect_for_date(league_code, day))
            day += timedelta(days=1)
        return matches

    def _collect_live(self, league_code: str) -> list[MatchPayload]:
        league_id = self._league_id(league_code)
        matches: list[MatchPayload] = []
        for item in self._paginate(
            f"{self._base_url}/fixtures/live",
            params={"leagueId": league_id},
            context=f"fixtures/live league={league_code}",
        ):
            parsed = parse_goal_api_fixture(item)
            if parsed is not None:
                matches.append(parsed)
        return matches

    def _collect_for_date(self, league_code: str, day: date) -> list[MatchPayload]:
        league_id = self._league_id(league_code)
        matches: list[MatchPayload] = []
        for item in self._paginate(
            f"{self._base_url}/fixtures/date/{day.isoformat()}",
            params={"leagueId": league_id},
            context=f"fixtures/date/{day.isoformat()} league={league_code}",
        ):
            # Prefer server filter; still guard if leagueId is ignored by the API.
            if item.get("leagueId") not in (None, league_id):
                continue
            parsed = parse_goal_api_fixture(item)
            if parsed is not None:
                matches.append(parsed)
        return matches

    def _paginate(
        self,
        url: str,
        *,
        params: dict[str, Any],
        context: str,
    ) -> Iterator[dict[str, Any]]:
        offset = 0
        while True:
            page_params = {
                **params,
                "limit": self._page_limit,
                "offset": offset,
            }
            payload = self._http.get_json(
                url,
                headers=self._headers(),
                params=page_params,
            )
            if not isinstance(payload, dict):
                raise RuntimeError(f"Goal API {context} returned a non-object payload.")
            raise_for_goal_api_errors(payload, context=context)
            rows = payload.get("data") or []
            if not isinstance(rows, list):
                raise RuntimeError(f"Goal API {context} returned a non-list data payload.")
            for row in rows:
                if isinstance(row, dict):
                    yield row
            pagination = payload.get("pagination") or {}
            if not pagination.get("hasMore"):
                break
            page_limit = int(pagination.get("limit") or self._page_limit)
            offset = int(pagination.get("offset") or offset) + page_limit
            if not rows:
                break
