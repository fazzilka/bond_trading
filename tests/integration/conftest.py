from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from bond_trading.api.auth_dependencies import (
    get_authenticated_session,
    get_current_web_auth,
)
from bond_trading.application.services.auth import AuthenticatedSession
from bond_trading.application.services.imports import ImportPreviewCache
from bond_trading.infrastructure.db import Base
from bond_trading.infrastructure.db.models import AuthSessionModel, UserModel, UserRole
from bond_trading.infrastructure.db.session import get_session
from bond_trading.infrastructure.google_sheets import MemoryGoogleSheetsGateway
from bond_trading.infrastructure.storage import MemoryObjectStorage
from bond_trading.main import create_app


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest_asyncio.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest_asyncio.fixture
async def app_client(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[tuple[httpx.AsyncClient, async_sessionmaker[AsyncSession], Any]]:
    app = create_app()

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_session] = override_session
    now = datetime.now(UTC)
    test_user = UserModel(
        id=UUID("11111111-1111-1111-1111-111111111111"),
        username="test-user",
        email="test@example.com",
        password_hash="unused",
        role=UserRole.ADMIN,
        is_active=True,
        must_change_password=False,
        created_at=now,
        updated_at=now,
    )
    test_auth = AuthenticatedSession(
        user=test_user,
        session=AuthSessionModel(
            id=UUID("22222222-2222-2222-2222-222222222222"),
            user_id=test_user.id,
            token_hash="unused",
            csrf_token_hash="unused",
            created_at=now,
            expires_at=now + timedelta(hours=1),
            last_seen_at=now,
        ),
        via_bearer=True,
    )

    async def override_auth() -> AuthenticatedSession:
        return test_auth

    app.dependency_overrides[get_authenticated_session] = override_auth
    app.dependency_overrides[get_current_web_auth] = override_auth
    app.state.import_cache = ImportPreviewCache(1800)
    app.state.moex_client = None
    app.state.object_storage = MemoryObjectStorage()
    app.state.google_sheets_gateway = MemoryGoogleSheetsGateway()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, session_factory, app
