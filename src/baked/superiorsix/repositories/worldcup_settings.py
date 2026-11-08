from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from baked.superiorsix.db.models import WorldcupSettingsRecord
from baked.superiorsix.domain.worldcup import WorldcupSettings

_SETTINGS_ID = 1


class WorldcupSettingsRepository(Protocol):
    async def get(self) -> WorldcupSettings: ...

    async def save(self, settings: WorldcupSettings) -> WorldcupSettings: ...


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
            )
        return _to_domain(record)

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
        await self._session.commit()
        await self._session.refresh(record)
        return _to_domain(record)


def _to_domain(record: WorldcupSettingsRecord) -> WorldcupSettings:
    return WorldcupSettings(
        id=record.id,
        buy_in_pence=record.buy_in_pence,
        participant_count=record.participant_count,
        open=bool(record.open),
    )
