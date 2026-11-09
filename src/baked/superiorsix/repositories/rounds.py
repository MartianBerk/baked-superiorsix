from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import Protocol
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from baked.superiorsix.db.models import (
    MatchRecord,
    PredictionRecord,
    RoundRecord,
    RoundWinnerRecord,
)
from baked.superiorsix.domain.models import Round


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min, tzinfo=UTC)
    return start, start + timedelta(days=1)


class RoundRepository(Protocol):
    async def get(self, round_id: int) -> Round | None: ...

    async def current(self) -> Round | None: ...

    async def list(self) -> list[Round]: ...

    async def add(self, round_: Round) -> Round: ...

    async def save(self, round_: Round) -> Round: ...

    async def end_with_winners(self, round_: Round, winner_ids: list[UUID]) -> Round: ...


class SqlAlchemyRoundRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, round_id: int) -> Round | None:
        record = await self._session.get(RoundRecord, round_id)
        if record is None:
            return None
        return await self._to_domain(record)

    async def current(self) -> Round | None:
        result = await self._session.execute(
            select(RoundRecord)
            .where(RoundRecord.end_date.is_(None))
            .order_by(RoundRecord.start_date.desc())
        )
        record = result.scalars().first()
        if record is None:
            return None
        return await self._to_domain(record)

    async def list(self) -> list[Round]:
        result = await self._session.execute(select(RoundRecord).order_by(RoundRecord.start_date))
        return [await self._to_domain(record) for record in result.scalars().all()]

    async def add(self, round_: Round) -> Round:
        record = RoundRecord(
            start_date=round_.start_date,
            end_date=round_.end_date,
            buy_in_pence=round_.buy_in_pence,
            amount_won=round_.amount_won,
        )
        self._session.add(record)
        await self._session.commit()
        await self._session.refresh(record)
        return await self._to_domain(record)

    async def save(self, round_: Round) -> Round:
        if round_.id is None:
            return await self.add(round_)
        record = await self._session.get(RoundRecord, round_.id)
        if record is None:
            return await self.add(round_)
        record.start_date = round_.start_date
        record.end_date = round_.end_date
        record.buy_in_pence = round_.buy_in_pence
        record.amount_won = round_.amount_won
        await self._session.commit()
        await self._session.refresh(record)
        return await self._to_domain(record)

    async def end_with_winners(self, round_: Round, winner_ids: list[UUID]) -> Round:
        if round_.id is None:
            raise ValueError("round id is required to record winners")
        record = await self._session.get(RoundRecord, round_.id)
        if record is None:
            raise ValueError(f"round {round_.id} not found")
        record.start_date = round_.start_date
        record.end_date = round_.end_date
        record.buy_in_pence = round_.buy_in_pence
        for auth_id in winner_ids:
            self._session.add(RoundWinnerRecord(round_id=round_.id, auth_id=auth_id))
        # Freeze display pot at close (same formula Stats used when amount_won is unset).
        record.amount_won = await self._compute_amount_won(record)
        await self._session.commit()
        await self._session.refresh(record)
        return await self._to_domain(record)

    async def _compute_amount_won(self, record: RoundRecord) -> int:
        assert record.end_date is not None
        players = await self._session.scalar(
            select(func.count(func.distinct(PredictionRecord.auth_id))).where(
                PredictionRecord.round_id == record.id
            )
        )
        match_day = func.date(MatchRecord.match_date)
        start, _ = _day_bounds(record.start_date)
        _, end = _day_bounds(record.end_date)
        matches = await self._session.scalar(
            select(func.count(func.distinct(match_day))).where(
                MatchRecord.use_match.is_(True),
                MatchRecord.match_date >= start,
                MatchRecord.match_date < end,
            )
        )
        return record.buy_in_pence * int(matches or 0) * int(players or 0)

    async def _winner_ids(self, round_id: int) -> list[UUID]:
        result = await self._session.execute(
            select(RoundWinnerRecord.auth_id)
            .where(RoundWinnerRecord.round_id == round_id)
            .order_by(RoundWinnerRecord.auth_id)
        )
        return list(result.scalars().all())

    async def _to_domain(self, record: RoundRecord) -> Round:
        winners = await self._winner_ids(record.id)
        return Round(
            id=record.id,
            start_date=record.start_date,
            end_date=record.end_date,
            buy_in_pence=record.buy_in_pence,
            amount_won=record.amount_won,
            winner_ids=winners,
        )
