from zoneinfo import ZoneInfo

import httpx
import pytest
import respx

from bond_trading.core.config import MoexSettings
from bond_trading.infrastructure.moex.client import MoexIssClient

from .test_mapper import payloads


@pytest.mark.asyncio
@respx.mock
async def test_client_retries_temporary_failure_and_caches() -> None:
    search, specification, market, bondization = payloads()
    settings = MoexSettings(retries=2, market_ttl_seconds=900)
    async with httpx.AsyncClient(base_url="https://iss.moex.com/iss") as http_client:
        client = MoexIssClient(http_client, settings, ZoneInfo("Europe/Moscow"))
        search_route = respx.get(path="/iss/securities.json").mock(
            side_effect=[httpx.Response(503), httpx.Response(200, json=search)]
        )
        spec_route = respx.get(path="/iss/securities/RU000A107SX3.json").mock(
            return_value=httpx.Response(200, json=specification)
        )
        market_route = respx.get(
            path="/iss/engines/stock/markets/bonds/boards/TQCB/securities/RU000A107SX3.json"
        ).mock(return_value=httpx.Response(200, json=market))
        action_route = respx.get(
            path=("/iss/statistics/engines/stock/markets/bonds/bondization/RU000A107SX3.json")
        ).mock(return_value=httpx.Response(200, json=bondization))

        first = await client.refresh("ru000a107sx3")
        second = await client.refresh("RU000A107SX3", force=False)

    assert first is second
    assert search_route.call_count == 2
    assert spec_route.call_count == 1
    assert market_route.call_count == 1
    assert action_route.call_count == 1
