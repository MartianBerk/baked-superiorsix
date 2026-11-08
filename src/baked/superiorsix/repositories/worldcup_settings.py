from typing import Protocol
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from baked.superiorsix.db.models import WorldcupSettingsRecord, WorldcupWinnerRecord
from baked.superiorsix.domain.worldcup import WorldcupSettings

_SETTINGS_ID = 1


class WorldcupSettingsRepository(Protocol):
    async def get(self) -> WorldcupSettings: ...

    async def save(self, settings: WorldcupSettings) -> WorldcupSettings: ...

    async def end_with_winners(
        self,
        settings: WorldcupSettings,
        winner_ids: list[UUID],
    ) -> WorldcupSettings: ...


class SqlAlchemyWorldcupSettingsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self) -> WorldcupSettings:
        record = await self._session.get(WorldcupSettingsRecord, _SETTINGS_ID)
        if record is None:
            return WorldcupSettings(
                buy_in_pence=0,
                participant_count=0,
                id=_SETTINGS_ID,
                open=False,
                winner_ids=[],
            )
        return await self._to_domain(record)

    async def save(self, settings: WorldcupSettings) -> WorldcupSettings:
        record = await self._session.get(WorldcupSettingsRecord, _SETTINGS_ID)
        if record is None:
            record = WorldcupSettingsRecord(
                id=_SETTINGS_ID,
                buy_in_pence=settings.buy_in_pence,
                participant_count=settings.participant_count,
                open=settings.open,
            )
            self._session.add(record)
        else:
            record.buy_in_pence = settings.buy_in_pence
            record.participant_count = settings.participant_count
            record.open = settings.open
        # Opening a new edition clears prior winners until the next close.
        if settings.open:
            await self._session.execute(
                delete(WorldcupWinnerRecord).where(
                    WorldcupWinnerRecord.settings_id == _SETTINGS_ID
                )
            )
        await self._session.commit()
        await self._session.refresh(record)
        return await self._to_domain(record)

    async def end_with_winners(
        self,
        settings: WorldcupSettings,
        winner_ids: list[UUID],
    ) -> WorldcupSettings:
        record = await self._session.get(WorldcupSettingsRecord, _SETTINGS_ID)
        if record is None:
            record = WorldcupSettingsRecord(
                id=_SETTINGS_ID,
                buy_in_pence=settings.buy_in_pence,
                participant_count=settings.participant_count,
                open=False,
            )
            self._session.add(record)
        else:
            record.buy_in_pence = settings.buy_in_pence
            record.participant_count = settings.participant_count
            record.open = False
        await self._session.execute(
            delete(WorldcupWinnerRecord).where(
                WorldcupWinnerRecord.settings_id == _SETTINGS_ID
            )
        )
        for auth_id in winner_ids:
            self._session.add(
                WorldcupWinnerRecord(settings_id=_SETTINGS_ID, auth_id=auth_id)
            )
        await self._session.commit()
        await self._session.refresh(record)
        return await self._to_domain(record)

    async def _winner_ids(self) -> list[UUID]:
        result = await self._session.execute(
            select(WorldcupWinnerRecord.auth_id)
            .where(WorldcupWinnerRecord.settings_id == _SETTINGS_ID)
            .order_by(WorldcupWinnerRecord.auth_id)
        )
        return list(result.scalars().all())

    async def _to_domain(self, record: WorldcupSettingsRecord) -> WorldcupSettings:
        return WorldcupSettings(
            id=record.id,
            buy_in_pence=record.buy_in_pence,
            participant_count=record.participant_count,
            open=bool(record.open),
            winner_ids=await self._winner_ids(),
        )
