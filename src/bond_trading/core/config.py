from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, SecretStr, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

BASE_DIR = Path(__file__).resolve().parents[3]
CONFIG_PATH = BASE_DIR / "config.toml"


class DatabaseSettings(BaseModel):
    url: str = "postgresql+asyncpg://bond_trading:bond_trading@127.0.0.1:5432/bond_trading"
    echo: bool = False


class MoexSettings(BaseModel):
    base_url: str = "https://iss.moex.com/iss"
    passport_auth_url: str = "https://passport.moex.com/authenticate"
    passport_login: str | None = None
    passport_password: SecretStr | None = None
    require_auth: bool = False
    timeout_seconds: float = 10.0
    retries: int = Field(default=3, ge=1, le=5)
    concurrency: int = Field(default=5, ge=1, le=20)
    reference_ttl_seconds: int = Field(default=21_600, ge=60)
    market_ttl_seconds: int = Field(default=900, ge=60)
    user_agent: str = "bond-trading/0.1"

    @model_validator(mode="after")
    def validate_passport_credentials(self) -> "MoexSettings":
        has_login = bool(self.passport_login and self.passport_login.strip())
        has_password = bool(self.passport_password and self.passport_password.get_secret_value())
        if has_login != has_password:
            raise ValueError("MOEX Passport login and password must be configured together")
        if self.require_auth and not has_login:
            raise ValueError("MOEX Passport credentials are required when require_auth=true")
        return self

    @property
    def has_passport_credentials(self) -> bool:
        return bool(
            self.passport_login
            and self.passport_login.strip()
            and self.passport_password
            and self.passport_password.get_secret_value()
        )


class ImportSettings(BaseModel):
    max_upload_bytes: int = Field(default=10 * 1024 * 1024, ge=1024)
    preview_ttl_seconds: int = Field(default=1800, ge=60)


class StorageSettings(BaseModel):
    endpoint: str = "127.0.0.1:9000"
    access_key: str = "bond-trading"
    secret_key: str = "change-me-minio"
    bucket: str = "bond-trading-uploads"
    secure: bool = False
    region: str = "us-east-1"


class AuthSettings(BaseModel):
    session_ttl_seconds: int = Field(default=7 * 24 * 60 * 60, ge=300)
    session_cookie_name: str = "bond_trading_session"
    csrf_cookie_name: str = "bond_trading_csrf"
    secure_cookies: bool = False
    bootstrap_admin_username: str = "admin"
    bootstrap_admin_email: str = "admin@bond-trading.local"
    bootstrap_admin_password: SecretStr = SecretStr("")
    bootstrap_user1_username: str = "user1"
    bootstrap_user1_email: str = "user1@bond-trading.local"
    bootstrap_user1_password: SecretStr = SecretStr("")
    bootstrap_user2_username: str = "user2"
    bootstrap_user2_email: str = "user2@bond-trading.local"
    bootstrap_user2_password: SecretStr = SecretStr("")


class LoggingSettings(BaseModel):
    level: str = "INFO"


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="BOND_TRADING__",
        env_nested_delimiter="__",
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        toml_file=CONFIG_PATH,
    )

    app_name: str = "Bond Trading"
    environment: str = "development"
    timezone: str = "Europe/Moscow"
    database: DatabaseSettings = DatabaseSettings()
    moex: MoexSettings = MoexSettings()
    imports: ImportSettings = ImportSettings()
    storage: StorageSettings = StorageSettings()
    auth: AuthSettings = AuthSettings()
    logging: LoggingSettings = LoggingSettings()

    @model_validator(mode="after")
    def require_production_secrets(self) -> "AppSettings":
        if self.environment.lower() != "production":
            return self
        if not self.auth.secure_cookies:
            raise ValueError("auth.secure_cookies must be enabled in production")
        secrets = (
            self.storage.secret_key,
            self.auth.bootstrap_admin_password.get_secret_value(),
            self.auth.bootstrap_user1_password.get_secret_value(),
            self.auth.bootstrap_user2_password.get_secret_value(),
        )
        if any(not secret or secret.startswith("change-me-") for secret in secrets):
            raise ValueError("default storage or bootstrap passwords are forbidden in production")
        return self

    @property
    def business_timezone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            TomlConfigSettingsSource(settings_cls),
            file_secret_settings,
        )


@lru_cache
def get_settings() -> AppSettings:
    return AppSettings()
