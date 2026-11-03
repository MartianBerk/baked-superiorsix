from __future__ import annotations

from datetime import date
from typing import Any, Protocol

from baked.superiorsix.process.extractors.connectors.dto import LeaguePayload, MatchPayload


class JsonHttpClient(Protocol):
    def get_json(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any: ...


class LeagueMatchConnector(Protocol):
    """Provider connector for league catalog + fixture pulls."""

    provider: str

    def collect_leagues(self) -> list[LeaguePayload]: ...

    def collect_matches(
        self,
        league_code: str,
        *,
        date_from: date,
        date_to: date,
        season_start: date | None = None,
        live: bool = False,
    ) -> list[MatchPayload]: ...
