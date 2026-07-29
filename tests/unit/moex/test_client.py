from zoneinfo import ZoneInfo

import httpx
import pytest
import respx

from bond_trading.core.config import MoexSettings
from bond_trading.infrastructure.moex.client import MoexIssClient
from bond_trading.infrastructure.moex.errors import MoexDataError

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


@pytest.mark.asyncio
@respx.mock
async def test_client_authenticates_with_moex_passport_cookie() -> None:
    settings = MoexSettings(
        passport_login="investor@example.com",
        passport_password="passport-secret",
        require_auth=True,
    )
    auth_route = respx.get(settings.passport_auth_url).mock(
        return_value=httpx.Response(
            200,
            headers={
                "set-cookie": (
                    "MicexPassportCert=test-certificate; Domain=.moex.com; Path=/; Secure; HttpOnly"
                )
            },
        )
    )
    async with httpx.AsyncClient(base_url=settings.base_url) as http_client:
        authenticated = await MoexIssClient(
            http_client, settings, ZoneInfo("Europe/Moscow")
        ).authenticate()

    assert authenticated is True
    assert auth_route.call_count == 1
    assert auth_route.calls[0].request.headers["Authorization"].startswith("Basic ")


@pytest.mark.asyncio
@respx.mock
async def test_client_rejects_passport_response_without_certificate() -> None:
    settings = MoexSettings(
        passport_login="investor@example.com",
        passport_password="wrong-secret",
    )
    respx.get(settings.passport_auth_url).mock(return_value=httpx.Response(200))
    async with httpx.AsyncClient(base_url=settings.base_url) as http_client:
        with pytest.raises(MoexDataError, match="MicexPassportCert"):
            await MoexIssClient(http_client, settings, ZoneInfo("Europe/Moscow")).authenticate()
