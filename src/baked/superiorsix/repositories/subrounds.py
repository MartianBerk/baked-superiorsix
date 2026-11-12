from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from baked.superiorsix.db.models import SubroundRecord
from baked.superiorsix.domain.models import Subround


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class SubroundRepository(Protocol):
    async def get(self, subround_id: int) -> Subround | None: ...

    async def list_for_round(self, round_id: int) -> list[Subround]: ...

    async def latest_for_round(self, round_id: int) -> Subround | None: ...

    async def next_sequence(self, round_id: int) -> int: ...

    async def add(self, subround: Subround) -> Subround: ...

    async def save(self, subround: Subround) -> Subround: ...

    async def delete(self, subround_id: int) -> None: ...


class SqlAlchemySubroundRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, subround_id: int) -> Subround | None:
        record = await self._session.get(SubroundRecord, subround_id)
        return _to_domain(record) if record else None

    async def list_for_round(self, round_id: int) -> list[Subround]:
        result = await self._session.execute(
            select(SubroundRecord)
            .where(SubroundRecord.round_id == round_id)
            .order_by(SubroundRecord.sequence)
        )
        return [_to_domain(record) for record in result.scalars().all()]

    async def latest_for_round(self, round_id: int) -> Subround | None:
        result = await self._session.execute(
            select(SubroundRecord)
            .where(SubroundRecord.round_id == round_id)
            .order_by(SubroundRecord.sequence.desc())
        )
        record = result.scalars().first()
        return _to_domain(record) if record else None

    async def next_sequence(self, round_id: int) -> int:
        result = await self._session.execute(
            select(func.coalesce(func.max(SubroundRecord.sequence), 0)).where(
                SubroundRecord.round_id == round_id
            )
        )
        return int(result.scalar_one()) + 1

    async def add(self, subround: Subround) -> Subround:
        record = SubroundRecord(
            round_id=subround.round_id,
            sequence=subround.sequence,
            created_at=subround.created_at or datetime.now(UTC),
            closed_at=subround.closed_at,
        )
        self._session.add(record)
        await self._session.commit()
        await self._session.refresh(record)
        return _to_domain(record)

    async def save(self, subround: Subround) -> Subround:
        if subround.id is None:
            return await self.add(subround)
        record = await self._session.get(SubroundRecord, subround.id)
        if record is None:
            return await self.add(subround)
        record.round_id = subround.round_id
        record.sequence = subround.sequence
        record.closed_at = subround.closed_at
        await self._session.commit()
        await self._session.refresh(record)
        return _to_domain(record)

    async def delete(self, subround_id: int) -> None:
        record = await self._session.get(SubroundRecord, subround_id)
        if record is None:
            return
        await self._session.delete(record)
        await self._session.commit()


def _to_domain(record: SubroundRecord) -> Subround:
    return Subround(
        id=record.id,
        round_id=record.round_id,
        sequence=record.sequence,
        created_at=_as_utc(record.created_at),
        closed_at=_as_utc(record.closed_at),
    )
