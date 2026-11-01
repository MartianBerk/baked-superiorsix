from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from baked.superiorsix.db.models import RoundRecord
from baked.superiorsix.domain.models import Round


class RoundRepository(Protocol):
    async def get(self, round_id: int) -> Round | None: ...

    async def current(self) -> Round | None: ...

    async def list(self) -> list[Round]: ...

    async def add(self, round_: Round) -> Round: ...

    async def save(self, round_: Round) -> Round: ...


class SqlAlchemyRoundRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, round_id: int) -> Round | None:
        record = await self._session.get(RoundRecord, round_id)
        return _to_domain(record) if record else None

    async def current(self) -> Round | None:
        result = await self._session.execute(
            select(RoundRecord)
            .where(RoundRecord.end_date.is_(None))
            .order_by(RoundRecord.start_date.desc())
        )
        record = result.scalars().first()
        return _to_domain(record) if record else None

    async def list(self) -> list[Round]:
        result = await self._session.execute(select(RoundRecord).order_by(RoundRecord.start_date))
        return [_to_domain(record) for record in result.scalars().all()]

    async def add(self, round_: Round) -> Round:
        record = RoundRecord(
            start_date=round_.start_date,
            end_date=round_.end_date,
            buy_in_pence=round_.buy_in_pence,
        )
        self._session.add(record)
        await self._session.commit()
        await self._session.refresh(record)
        return _to_domain(record)

    async def save(self, round_: Round) -> Round:
        if round_.id is None:
            return await self.add(round_)
        record = await self._session.get(RoundRecord, round_.id)
        if record is None:
            return await self.add(round_)
        record.start_date = round_.start_date
        record.end_date = round_.end_date
        record.buy_in_pence = round_.buy_in_pence
        await self._session.commit()
        await self._session.refresh(record)
        return _to_domain(record)


def _to_domain(record: RoundRecord) -> Round:
    return Round(
        id=record.id,
        start_date=record.start_date,
        end_date=record.end_date,
        buy_in_pence=record.buy_in_pence,
    )
