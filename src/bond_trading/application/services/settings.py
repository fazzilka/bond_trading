from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bond_trading.infrastructure.db.models import AppSettingModel


class SettingsService:
    def __init__(self, session: AsyncSession, owner_id: UUID) -> None:
        self._session = session
        self._owner_id = owner_id

    async def get(self) -> AppSettingModel:
        value = await self._session.scalar(
            select(AppSettingModel).where(
                AppSettingModel.singleton_key == "default",
                AppSettingModel.owner_id == self._owner_id,
            )
        )
        if value is None:
            value = AppSettingModel(singleton_key="default", owner_id=self._owner_id)
            self._session.add(value)
            await self._session.flush()
        return value
