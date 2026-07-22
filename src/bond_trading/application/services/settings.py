from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bond_trading.infrastructure.db.models import AppSettingModel


class SettingsService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self) -> AppSettingModel:
        value = await self._session.scalar(
            select(AppSettingModel).where(AppSettingModel.singleton_key == "default")
        )
        if value is None:
            value = AppSettingModel(singleton_key="default")
            self._session.add(value)
            await self._session.flush()
        return value
