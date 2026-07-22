from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field
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
    timeout_seconds: float = 10.0
    retries: int = Field(default=3, ge=1, le=5)
    concurrency: int = Field(default=5, ge=1, le=20)
    reference_ttl_seconds: int = Field(default=21_600, ge=60)
    market_ttl_seconds: int = Field(default=900, ge=60)
    user_agent: str = "bond-trading/0.1"


class ImportSettings(BaseModel):
    max_upload_bytes: int = Field(default=10 * 1024 * 1024, ge=1024)
    preview_ttl_seconds: int = Field(default=1800, ge=60)


class LoggingSettings(BaseModel):
    level: str = "INFO"


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="BOND_TRADING__",
        env_nested_delimiter="__",
        extra="ignore",
        toml_file=CONFIG_PATH,
    )

    app_name: str = "Bond Trading"
    environment: str = "development"
    timezone: str = "Europe/Moscow"
    database: DatabaseSettings = DatabaseSettings()
    moex: MoexSettings = MoexSettings()
    imports: ImportSettings = ImportSettings()
    logging: LoggingSettings = LoggingSettings()

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
            TomlConfigSettingsSource(settings_cls),
            file_secret_settings,
        )


@lru_cache
def get_settings() -> AppSettings:
    return AppSettings()
