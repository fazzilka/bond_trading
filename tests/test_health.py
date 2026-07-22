import httpx
import pytest

from bond_trading.main import create_app


@pytest.mark.asyncio
async def test_health() -> None:
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "success"}
    assert response.headers["x-request-id"]
