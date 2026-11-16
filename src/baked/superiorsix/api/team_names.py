"""Helpers to put display / stats-key names on API match payloads.

Match responses should expose ``display()`` / ``load_display()`` names so the
FE never applies ``meta.teams`` mappings itself. Display matches ``stats_key``
(xref, then ``normalize_team_name``). Stored match rows keep original API names.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import TypeVar

from baked.superiorsix.domain.models import Match
from baked.superiorsix.services.team_names import normalize_team_name
from baked.superiorsix.services.team_xref import TeamXrefService, stats_key_for

T = TypeVar("T")

DisplayTeam = Callable[[str], str]


def identity_display(name: str) -> str:
    """Normalize only (no xref) — used when team_xref is unavailable."""
    return normalize_team_name(name)


def display_from_map(mapping: Mapping[str, str]) -> DisplayTeam:
    """Xref map then normalize — same identity as league-table ``stats_key``."""

    def display(name: str) -> str:
        return stats_key_for(name, mapping)

    return display


async def load_display(team_xref: TeamXrefService | None) -> DisplayTeam:
    if team_xref is None:
        return identity_display
    mapping = await team_xref.as_map()
    return display_from_map(mapping)


def with_display_teams(match: Match, display: DisplayTeam) -> Match:
    """Shallow copy with home/away replaced by display names for response building."""
    return Match(
        id=match.id,
        external_id=match.external_id,
        league_id=match.league_id,
        match_date=match.match_date,
        match_minute=match.match_minute,
        status=match.status,
        home_team=display(match.home_team),
        away_team=display(match.away_team),
        use_match=match.use_match,
        home_score=match.home_score,
        away_score=match.away_score,
        game_number=match.game_number,
        last_updated=match.last_updated,
        subround_id=match.subround_id,
        difficulty_score=match.difficulty_score,
        difficulty_band=match.difficulty_band,
    )
