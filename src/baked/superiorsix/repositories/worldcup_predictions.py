from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from baked.superiorsix.db.models import WorldcupPredictionRecord
from baked.superiorsix.domain.worldcup import WorldcupPrediction


class WorldcupPredictionRepository(Protocol):
    async def get(self, prediction_id: int) -> WorldcupPrediction | None: ...

    async def get_active(
        self,
        *,
        auth_id: UUID,
        match_id: int,
    ) -> WorldcupPrediction | None: ...

    async def get_row(
        self,
        *,
        auth_id: UUID,
        match_id: int,
    ) -> WorldcupPrediction | None: ...

    async def list_active(self) -> list[WorldcupPrediction]: ...

    async def add(self, prediction: WorldcupPrediction) -> WorldcupPrediction: ...

    async def save(self, prediction: WorldcupPrediction) -> WorldcupPrediction: ...


class SqlAlchemyWorldcupPredictionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, prediction_id: int) -> WorldcupPrediction | None:
        record = await self._session.get(WorldcupPredictionRecord, prediction_id)
        return _to_domain(record) if record else None

    async def get_active(
        self,
        *,
        auth_id: UUID,
        match_id: int,
    ) -> WorldcupPrediction | None:
        result = await self._session.execute(
            select(WorldcupPredictionRecord).where(
                WorldcupPredictionRecord.auth_id == auth_id,
                WorldcupPredictionRecord.match_id == match_id,
                WorldcupPredictionRecord.dropped.is_(False),
            )
        )
        record = result.scalar_one_or_none()
        return _to_domain(record) if record else None

    async def get_row(
        self,
        *,
        auth_id: UUID,
        match_id: int,
    ) -> WorldcupPrediction | None:
        result = await self._session.execute(
            select(WorldcupPredictionRecord).where(
                WorldcupPredictionRecord.auth_id == auth_id,
                WorldcupPredictionRecord.match_id == match_id,
            )
        )
        record = result.scalar_one_or_none()
        return _to_domain(record) if record else None

    async def list_active(self) -> list[WorldcupPrediction]:
        result = await self._session.execute(
            select(WorldcupPredictionRecord).where(
                WorldcupPredictionRecord.dropped.is_(False),
            )
        )
        return [_to_domain(record) for record in result.scalars().all()]

    async def add(self, prediction: WorldcupPrediction) -> WorldcupPrediction:
        record = WorldcupPredictionRecord(
            auth_id=prediction.auth_id,
            match_id=prediction.match_id,
            prediction=prediction.prediction,
            plus_ninety=prediction.plus_ninety,
            extra_time=prediction.extra_time,
            penalties=prediction.penalties,
            dropped=prediction.dropped,
        )
        self._session.add(record)
        await self._session.commit()
        await self._session.refresh(record)
        return _to_domain(record)

    async def save(self, prediction: WorldcupPrediction) -> WorldcupPrediction:
        if prediction.id is None:
            return await self.add(prediction)
        record = await self._session.get(WorldcupPredictionRecord, prediction.id)
        if record is None:
            return await self.add(prediction)
        record.auth_id = prediction.auth_id
        record.match_id = prediction.match_id
        record.prediction = prediction.prediction
        record.plus_ninety = prediction.plus_ninety
        record.extra_time = prediction.extra_time
        record.penalties = prediction.penalties
        record.dropped = prediction.dropped
        await self._session.commit()
        await self._session.refresh(record)
        return _to_domain(record)


def _to_domain(record: WorldcupPredictionRecord) -> WorldcupPrediction:
    return WorldcupPrediction(
        id=record.id,
        auth_id=record.auth_id,
        match_id=record.match_id,
        prediction=record.prediction,
        plus_ninety=record.plus_ninety,
        extra_time=record.extra_time,
        penalties=record.penalties,
        dropped=record.dropped,
    )
