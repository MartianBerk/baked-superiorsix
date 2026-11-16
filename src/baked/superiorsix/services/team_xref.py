from __future__ import annotations

from collections.abc import Mapping

from baked.superiorsix.domain.models import TeamXref
from baked.superiorsix.repositories.team_xref import TeamXrefRepository
from baked.superiorsix.services.team_names import normalize_team_name, team_name_variants


class TeamXrefService:
    """Stable provider team_name → xref for display labels and stats aggregation."""

    def __init__(self, team_xref: TeamXrefRepository) -> None:
        self._team_xref = team_xref

    async def list(self) -> list[TeamXref]:
        return await self._team_xref.list()

    async def as_map(self) -> dict[str, str]:
        """Legacy ``meta.teams`` shape: provider name → display / stats key."""
        return {row.team_name: row.xref for row in await self._team_xref.list()}

    async def canonical(self, name: str) -> str:
        """Display / stats identity: xref map, then strip trailing ``FC`` / ``AFC``."""
        return await self.stats_key(name)

    async def display(self, name: str) -> str:
        """FE label for a provider team name (same as ``stats_key``)."""
        return await self.stats_key(name)

    async def stats_key(self, name: str, team_canonical: Mapping[str, str] | None = None) -> str:
        """League-table / position / display identity for ``name``.

        Applies ``team_xref`` when present, then strips trailing ``FC`` / ``AFC``
        so Flashscore-style short names and football-data long names share a key.
        Stored match rows keep the original API spelling; this is read-path only.
        """
        mapping = team_canonical if team_canonical is not None else await self.as_map()
        return stats_key_for(name, mapping)

    async def aliases(self, name: str) -> list[str]:
        """All provider names that share ``name``'s stats identity (includes ``name``)."""
        key = await self.stats_key(name)
        names = set(team_name_variants(name)) | set(team_name_variants(key))
        # Xref column may still hold long-form labels; probe both key and variants.
        xref_keys = {key, *team_name_variants(key)}
        for xref in xref_keys:
            for row in await self._team_xref.list_by_xref(xref):
                names.update(team_name_variants(row.team_name))
                names.add(row.xref)
                names.add(normalize_team_name(row.xref))
        return sorted(names)

    async def upsert(self, mapping: TeamXref) -> TeamXref:
        return await self._team_xref.upsert(mapping)

    async def upsert_many(self, mappings: list[TeamXref]) -> list[TeamXref]:
        return await self._team_xref.upsert_many(mappings)


def stats_key_for(name: str, team_canonical: Mapping[str, str] | None) -> str:
    """Map a provider team string onto the league-table aggregation key."""
    if team_canonical:
        if name in team_canonical:
            return normalize_team_name(team_canonical[name])
        normalized = normalize_team_name(name)
        if normalized in team_canonical:
            return normalize_team_name(team_canonical[normalized])
        for suffix in (" FC", " AFC"):
            alt = f"{normalized}{suffix}"
            if alt in team_canonical:
                return normalize_team_name(team_canonical[alt])
        return normalized
    return normalize_team_name(name)
