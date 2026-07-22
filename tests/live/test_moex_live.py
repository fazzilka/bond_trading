import os
from zoneinfo import ZoneInfo

import httpx
import pytest

from bond_trading.core.config import MoexSettings
from bond_trading.infrastructure.moex import MoexIssClient

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.getenv("RUN_LIVE_MOEX") != "1",
        reason="Set RUN_LIVE_MOEX=1 to access MOEX ISS",
    ),
]


@pytest.mark.parametrize(
    "isin",
    ["RU000A107EW5", "RU000A106CJ8", "RU000A107SX3", "RU000A10ASF9", "RU000A107SG8"],
)
async def test_initial_instruments_are_available_from_moex(isin: str) -> None:
    settings = MoexSettings()
    async with httpx.AsyncClient(
        base_url=settings.base_url,
        timeout=settings.timeout_seconds,
        headers={"User-Agent": settings.user_agent},
    ) as http_client:
        result = await MoexIssClient(
            http_client, settings, ZoneInfo("Europe/Moscow")
        ).refresh(isin)

    assert result.instrument.isin == isin
