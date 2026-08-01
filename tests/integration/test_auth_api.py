from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bond_trading.api.auth_dependencies import get_authenticated_session
from bond_trading.application.services.auth import AuthenticationError, AuthService
from bond_trading.core.config import AuthSettings
from bond_trading.infrastructure.db.models import UserRole


async def _create_user(
    session_factory: async_sessionmaker[AsyncSession],
    username: str,
    role: UserRole = UserRole.USER,
) -> None:
    async with session_factory() as session:
        await AuthService(session, AuthSettings()).create_user(
            username=username,
            email=f"{username}@example.com",
            password=f"{username}-password-2026",
            role=role,
            must_change_password=False,
        )


async def _login(client: httpx.AsyncClient, username: str) -> dict[str, Any]:
    response = await client.post(
        "/api/v1/auth/login",
        json={"login": username, "password": f"{username}-password-2026"},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_login_csrf_roles_and_portfolio_isolation(
    app_client: tuple[
        httpx.AsyncClient,
        async_sessionmaker[AsyncSession],
        FastAPI,
    ],
) -> None:
    client, session_factory, app = app_client
    app.dependency_overrides.pop(get_authenticated_session)
    await _create_user(session_factory, "alice")
    await _create_user(session_factory, "bob")
    await _create_user(session_factory, "root-admin", UserRole.ADMIN)

    alice = await _login(client, "alice")
    me = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {alice['access_token']}"},
    )
    assert me.status_code == 200
    assert me.json()["username"] == "alice"

    lot = {
        "isin": "RU000A107SX3",
        "purchase_date": "2026-05-25",
        "quantity": "40",
        "purchase_clean_price_rub_per_bond": "962.90",
        "purchase_accrued_interest_rub_per_bond": "3.51",
        "purchase_commission_rub_per_bond": "0.39",
        "target_event_type": "maturity",
        "target_event_date": "2027-02-15",
        "target_redemption_price_rub_per_bond": "1000",
        "target_redemption_override_reason": "Regression scenario",
    }
    without_csrf = await client.post("/api/v1/lots", json=lot)
    assert without_csrf.status_code == 403

    created = await client.post(
        "/api/v1/lots",
        json=lot,
        headers={"Authorization": f"Bearer {alice['access_token']}"},
    )
    assert created.status_code == 201, created.text

    bob = await _login(client, "bob")
    bob_lots = await client.get(
        "/api/v1/lots",
        headers={"Authorization": f"Bearer {bob['access_token']}"},
    )
    assert bob_lots.status_code == 200
    assert bob_lots.json() == []

    forbidden = await client.get(
        "/api/v1/admin/users",
        headers={"Authorization": f"Bearer {bob['access_token']}"},
    )
    assert forbidden.status_code == 403

    admin = await _login(client, "root-admin")
    users = await client.get(
        "/api/v1/admin/users",
        headers={"Authorization": f"Bearer {admin['access_token']}"},
    )
    assert users.status_code == 200
    assert {user["username"] for user in users.json()} == {
        "alice",
        "bob",
        "root-admin",
    }

    logout = await client.post(
        "/api/v1/auth/logout",
        headers={"Authorization": f"Bearer {alice['access_token']}"},
    )
    assert logout.status_code == 204
    expired = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {alice['access_token']}"},
    )
    assert expired.status_code == 401


async def test_bootstrap_admin_password_is_synchronized_from_settings(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    initial_settings = AuthSettings(
        bootstrap_admin_password="initial-admin-password-2026",
        bootstrap_user1_password="initial-user1-password-2026",
        bootstrap_user2_password="initial-user2-password-2026",
    )
    async with session_factory() as session:
        initial_service = AuthService(session, initial_settings)
        await initial_service.ensure_bootstrap_users()
        credentials = await initial_service.authenticate(
            "admin", "initial-admin-password-2026", "pytest"
        )

    rotated_settings = AuthSettings(
        bootstrap_admin_password="rotated-admin-password-2026",
        bootstrap_user1_password="initial-user1-password-2026",
        bootstrap_user2_password="initial-user2-password-2026",
    )
    async with session_factory() as session:
        rotated_service = AuthService(session, rotated_settings)
        await rotated_service.ensure_bootstrap_users()
        with pytest.raises(AuthenticationError):
            await rotated_service.resolve(credentials.token, via_bearer=True)
        with pytest.raises(AuthenticationError):
            await rotated_service.authenticate("admin", "initial-admin-password-2026", "pytest")
        new_credentials = await rotated_service.authenticate(
            "admin", "rotated-admin-password-2026", "pytest"
        )

    assert new_credentials.user.must_change_password is False
