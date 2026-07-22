import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


async def test_ui_pages_render(
    app_client: tuple[httpx.AsyncClient, async_sessionmaker[AsyncSession], object],
) -> None:
    client, _, _ = app_client

    for path, expected in (
        ("/portfolio", "Портфель облигаций"),
        ("/import", "Импорт портфеля"),
        ("/settings", "Настройки"),
        ("/data-status", "Статус рыночных данных"),
    ):
        response = await client.get(path)
        assert response.status_code == 200, response.text
        assert expected in response.text

    root = await client.get("/", follow_redirects=False)
    assert root.status_code == 307
    assert root.headers["location"] == "/portfolio"

    portfolio = await client.get("/portfolio")
    assert "Полученные купоны" in portfolio.text
    assert "Ликвидность" in portfolio.text
    assert "после налога (оценка)" in portfolio.text


async def test_portfolio_renders_liquidity_for_a_lot_without_market_data(
    app_client: tuple[httpx.AsyncClient, async_sessionmaker[AsyncSession], object],
) -> None:
    client, _, _ = app_client
    created = await client.post(
        "/api/v1/lots",
        json={
            "isin": "RU000A107SX3",
            "purchase_date": "2026-05-25",
            "quantity": "40",
            "purchase_clean_price_rub_per_bond": "962.90",
            "purchase_accrued_interest_rub_per_bond": "3.51",
            "purchase_commission_rub_per_bond": "0.39",
            "target_event_type": "maturity",
            "target_event_date": "2027-02-15",
        },
    )
    assert created.status_code == 201

    response = await client.get("/portfolio")
    assert response.status_code == 200
    assert "liquidity-none" in response.text
    assert "RU000A107SX3" in response.text
