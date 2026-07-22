from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from bond_trading.core.config import AppSettings


class Database:
    def __init__(self, settings: AppSettings) -> None:
        self.engine: AsyncEngine = create_async_engine(
            settings.database.url,
            echo=settings.database.echo,
            pool_pre_ping=True,
            pool_recycle=1800,
        )
        self.session_factory = async_sessionmaker(
            self.engine,
            expire_on_commit=False,
            autoflush=False,
        )

    async def close(self) -> None:
        await self.engine.dispose()


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    database: Database = request.app.state.database
    async with database.session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
