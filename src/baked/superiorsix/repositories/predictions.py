from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from baked.superiorsix.db.models import PredictionRecord
from baked.superiorsix.domain.models import Prediction


class PredictionRepository(Protocol):
    async def get(self, prediction_id: int) -> Prediction | None: ...

    async def get_active(
        self,
        *,
        round_id: int,
        auth_id: UUID,
        match_id: int,
    ) -> Prediction | None: ...

    async def add(self, prediction: Prediction) -> Prediction: ...

    async def save(self, prediction: Prediction) -> Prediction: ...


class SqlAlchemyPredictionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, prediction_id: int) -> Prediction | None:
        record = await self._session.get(PredictionRecord, prediction_id)
        return _to_domain(record) if record else None

    async def get_active(
        self,
        *,
        round_id: int,
        auth_id: UUID,
        match_id: int,
    ) -> Prediction | None:
        result = await self._session.execute(
            select(PredictionRecord).where(
                PredictionRecord.round_id == round_id,
                PredictionRecord.auth_id == auth_id,
                PredictionRecord.match_id == match_id,
                PredictionRecord.dropped.is_(False),
            )
        )
        record = result.scalar_one_or_none()
        return _to_domain(record) if record else None

    async def add(self, prediction: Prediction) -> Prediction:
        record = PredictionRecord(
            round_id=prediction.round_id,
            auth_id=prediction.auth_id,
            match_id=prediction.match_id,
            prediction=prediction.prediction,
            dropped=prediction.dropped,
        )
        self._session.add(record)
        await self._session.commit()
        await self._session.refresh(record)
        return _to_domain(record)

    async def save(self, prediction: Prediction) -> Prediction:
        if prediction.id is None:
            return await self.add(prediction)
        record = await self._session.get(PredictionRecord, prediction.id)
        if record is None:
            return await self.add(prediction)
        record.round_id = prediction.round_id
        record.auth_id = prediction.auth_id
        record.match_id = prediction.match_id
        record.prediction = prediction.prediction
        record.dropped = prediction.dropped
        await self._session.commit()
        await self._session.refresh(record)
        return _to_domain(record)


def _to_domain(record: PredictionRecord) -> Prediction:
    return Prediction(
        id=record.id,
        round_id=record.round_id,
        auth_id=record.auth_id,
        match_id=record.match_id,
        prediction=record.prediction,
        dropped=record.dropped,
    )
