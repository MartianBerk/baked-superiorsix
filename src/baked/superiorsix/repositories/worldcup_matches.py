from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from baked.superiorsix.db.models import WorldcupMatchRecord
from baked.superiorsix.domain.worldcup import WorldcupMatch


class WorldcupMatchRepository(Protocol):
    async def get(self, match_id: int) -> WorldcupMatch | None: ...

    async def get_by_external_id(self, external_id: str) -> WorldcupMatch | None: ...

    async def list_all(self) -> list[WorldcupMatch]: ...

    async def list_finished(self) -> list[WorldcupMatch]: ...

    async def add(self, match: WorldcupMatch) -> WorldcupMatch: ...

    async def save(self, match: WorldcupMatch) -> WorldcupMatch: ...


class SqlAlchemyWorldcupMatchRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, match_id: int) -> WorldcupMatch | None:
        record = await self._session.get(WorldcupMatchRecord, match_id)
        return _to_domain(record) if record else None

    async def get_by_external_id(self, external_id: str) -> WorldcupMatch | None:
        result = await self._session.execute(
            select(WorldcupMatchRecord).where(WorldcupMatchRecord.external_id == external_id)
        )
        record = result.scalar_one_or_none()
        return _to_domain(record) if record else None

    async def list_all(self) -> list[WorldcupMatch]:
        result = await self._session.execute(
            select(WorldcupMatchRecord).order_by(
                WorldcupMatchRecord.match_date,
                WorldcupMatchRecord.id,
            )
        )
        return [_to_domain(record) for record in result.scalars().all()]

    async def list_finished(self) -> list[WorldcupMatch]:
        result = await self._session.execute(
            select(WorldcupMatchRecord).where(WorldcupMatchRecord.status == "FINISHED")
        )
        return [_to_domain(record) for record in result.scalars().all()]

    async def add(self, match: WorldcupMatch) -> WorldcupMatch:
        record = WorldcupMatchRecord(
            external_id=match.external_id,
            league_id=match.league_id,
            matchday=match.matchday,
            match_date=match.match_date,
            match_minute=match.match_minute,
            status=match.status,
            home_team=match.home_team,
            away_team=match.away_team,
            use_match=match.use_match,
            home_score=match.home_score,
            away_score=match.away_score,
            extra_time=match.extra_time,
            penalties=match.penalties,
        )
        self._session.add(record)
        await self._session.commit()
        await self._session.refresh(record)
        return _to_domain(record)

    async def save(self, match: WorldcupMatch) -> WorldcupMatch:
        if match.id is None:
            return await self.add(match)
        record = await self._session.get(WorldcupMatchRecord, match.id)
        if record is None:
            return await self.add(match)
        record.external_id = match.external_id
        record.league_id = match.league_id
        record.matchday = match.matchday
        record.match_date = match.match_date
        record.match_minute = match.match_minute
        record.status = match.status
        record.home_team = match.home_team
        record.away_team = match.away_team
        record.use_match = match.use_match
        record.home_score = match.home_score
        record.away_score = match.away_score
        record.extra_time = match.extra_time
        record.penalties = match.penalties
        await self._session.commit()
        await self._session.refresh(record)
        return _to_domain(record)


def _to_domain(record: WorldcupMatchRecord) -> WorldcupMatch:
    return WorldcupMatch(
        id=record.id,
        external_id=record.external_id,
        league_id=record.league_id,
        matchday=record.matchday,
        match_date=record.match_date,
        match_minute=record.match_minute,
        status=record.status,
        home_team=record.home_team,
        away_team=record.away_team,
        use_match=record.use_match,
        home_score=record.home_score,
        away_score=record.away_score,
        extra_time=record.extra_time,
        penalties=record.penalties,
        last_updated=record.last_updated,
    )
