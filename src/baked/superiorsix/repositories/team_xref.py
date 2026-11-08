from __future__ import annotations

from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from baked.superiorsix.db.models import TeamXrefRecord
from baked.superiorsix.domain.models import TeamXref


class TeamXrefRepository(Protocol):
    async def list(self) -> list[TeamXref]: ...

    async def list_by_xref(self, xref: str) -> list[TeamXref]: ...

    async def get(self, team_name: str) -> TeamXref | None: ...

    async def upsert(self, mapping: TeamXref) -> TeamXref: ...

    async def upsert_many(self, mappings: list[TeamXref]) -> list[TeamXref]: ...


class SqlAlchemyTeamXrefRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list(self) -> list[TeamXref]:
        result = await self._session.execute(
            select(TeamXrefRecord).order_by(TeamXrefRecord.team_name)
        )
        return [_to_domain(record) for record in result.scalars().all()]

    async def list_by_xref(self, xref: str) -> list[TeamXref]:
        result = await self._session.execute(
            select(TeamXrefRecord)
            .where(TeamXrefRecord.xref == xref)
            .order_by(TeamXrefRecord.team_name)
        )
        return [_to_domain(record) for record in result.scalars().all()]

    async def get(self, team_name: str) -> TeamXref | None:
        record = await self._session.get(TeamXrefRecord, team_name)
        return _to_domain(record) if record else None

    async def upsert(self, mapping: TeamXref) -> TeamXref:
        record = await self._session.get(TeamXrefRecord, mapping.team_name)
        if record is None:
            record = TeamXrefRecord(team_name=mapping.team_name, xref=mapping.xref)
            self._session.add(record)
        else:
            record.xref = mapping.xref
        await self._session.commit()
        await self._session.refresh(record)
        return _to_domain(record)

    async def upsert_many(self, mappings: list[TeamXref]) -> list[TeamXref]:
        out: list[TeamXref] = []
        for mapping in mappings:
            record = await self._session.get(TeamXrefRecord, mapping.team_name)
            if record is None:
                record = TeamXrefRecord(team_name=mapping.team_name, xref=mapping.xref)
                self._session.add(record)
            else:
                record.xref = mapping.xref
            out.append(mapping)
        await self._session.commit()
        return sorted(out, key=lambda item: item.team_name)


def _to_domain(record: TeamXrefRecord) -> TeamXref:
    return TeamXref(team_name=record.team_name, xref=record.xref)
