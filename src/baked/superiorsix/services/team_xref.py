from __future__ import annotations

from baked.superiorsix.domain.models import TeamXref
from baked.superiorsix.repositories.team_xref import TeamXrefRepository


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
        """Return the xref for ``name``, or ``name`` when unmapped."""
        row = await self._team_xref.get(name)
        return name if row is None else row.xref

    async def display(self, name: str) -> str:
        """FE label for a provider team name (same as ``canonical``)."""
        return await self.canonical(name)

    async def aliases(self, name: str) -> list[str]:
        """All provider names that share ``name``'s canonical key (includes ``name``)."""
        key = await self.canonical(name)
        names = {name, key}
        for row in await self._team_xref.list_by_xref(key):
            names.add(row.team_name)
        return sorted(names)

    async def upsert(self, mapping: TeamXref) -> TeamXref:
        return await self._team_xref.upsert(mapping)

    async def upsert_many(self, mappings: list[TeamXref]) -> list[TeamXref]:
        return await self._team_xref.upsert_many(mappings)
