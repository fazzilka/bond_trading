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
