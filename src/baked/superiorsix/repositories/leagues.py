from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from baked.superiorsix.db.models import LeagueRecord
from baked.superiorsix.domain.models import League


class LeagueRepository(Protocol):
    async def get(self, league_id: int) -> League | None: ...

    async def get_by_code(self, code: str) -> League | None: ...

    async def list(self) -> list[League]: ...

    async def add(self, league: League) -> League: ...

    async def save(self, league: League) -> League: ...


class SqlAlchemyLeagueRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, league_id: int) -> League | None:
        record = await self._session.get(LeagueRecord, league_id)
        return _to_domain(record) if record else None

    async def get_by_code(self, code: str) -> League | None:
        result = await self._session.execute(select(LeagueRecord).where(LeagueRecord.code == code))
        record = result.scalar_one_or_none()
        return _to_domain(record) if record else None

    async def list(self) -> list[League]:
        result = await self._session.execute(select(LeagueRecord).order_by(LeagueRecord.code))
        return [_to_domain(record) for record in result.scalars().all()]

    async def add(self, league: League) -> League:
        record = LeagueRecord(
            name=league.name,
            code=league.code,
            start_date=league.start_date,
            current_matchday=league.current_matchday,
        )
        if league.id is not None:
            record.id = league.id
        self._session.add(record)
        await self._session.commit()
        await self._session.refresh(record)
        return _to_domain(record)

    async def save(self, league: League) -> League:
        if league.id is None:
            return await self.add(league)
        record = await self._session.get(LeagueRecord, league.id)
        if record is None:
            return await self.add(league)
        record.name = league.name
        record.code = league.code
        record.start_date = league.start_date
        record.current_matchday = league.current_matchday
        await self._session.commit()
        await self._session.refresh(record)
        return _to_domain(record)


def _to_domain(record: LeagueRecord) -> League:
    return League(
        id=record.id,
        name=record.name,
        code=record.code,
        start_date=record.start_date,
        current_matchday=record.current_matchday,
    )
