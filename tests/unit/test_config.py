import pytest
from pydantic import ValidationError

from bond_trading.core.config import AppSettings, AuthSettings, StorageSettings


def test_production_rejects_development_security_defaults() -> None:
    with pytest.raises(ValidationError, match="secure_cookies"):
        AppSettings(environment="production")


def test_production_accepts_explicit_security_settings() -> None:
    settings = AppSettings(
        environment="production",
        storage=StorageSettings(secret_key="local-production-storage-secret"),
        auth=AuthSettings(
            secure_cookies=True,
            bootstrap_admin_password="production-admin-secret",
            bootstrap_user1_password="production-user1-secret",
            bootstrap_user2_password="production-user2-secret",
        ),
    )

    assert settings.auth.secure_cookies is True
