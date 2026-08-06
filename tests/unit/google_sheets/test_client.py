from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest

from bond_trading.core.config import GoogleSheetsSettings
from bond_trading.infrastructure.google_sheets import (
    CellUpdate,
    GoogleServiceAccountSheetsGateway,
    GoogleSheetsError,
)


async def test_google_gateway_reads_isins_and_updates_only_requested_cells() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET" and "/values/" in request.url.path:
            return httpx.Response(200, json={"values": [["RU000A107SX3"], [], ["RU000A106CJ8"]]})
        if request.method == "POST" and request.url.path.endswith("/values:batchUpdate"):
            return httpx.Response(200, json={"totalUpdatedCells": 3})
        raise AssertionError(f"Неожиданный Google-запрос: {request.method} {request.url}")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://sheets.googleapis.com/v4",
    ) as http_client:
        gateway = GoogleServiceAccountSheetsGateway(GoogleSheetsSettings(), http_client)
        gateway._access_token = AsyncMock(return_value="test-token")  # type: ignore[method-assign]

        rows = await gateway.read_isins("spreadsheet-123", "Portfolio", "B", 3)
        updated = await gateway.update_cells(
            "spreadsheet-123",
            "Portfolio",
            [
                CellUpdate(3, "X", Decimal("972.30")),
                CellUpdate(3, "Y", "2026-08-01T12:00:00+03:00"),
                CellUpdate(3, "Z", "FRESH"),
            ],
        )

    assert [(row.row_number, row.raw_isin) for row in rows] == [
        (3, "RU000A107SX3"),
        (5, "RU000A106CJ8"),
    ]
    assert updated == 3
    body = requests[-1].read().decode()
    assert '"valueInputOption":"RAW"' in body
    assert "'Portfolio'!X3" in body
    assert "'Portfolio'!Y3" in body
    assert "'Portfolio'!Z3" in body
    assert "D3" not in body
    assert "formula" not in body.lower()


async def test_google_gateway_checks_existing_worksheet() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer test-token"
        return httpx.Response(
            200,
            json={
                "properties": {"title": "Портфель заказчика"},
                "sheets": [
                    {"properties": {"title": "Доход счёт 2026"}},
                    {"properties": {"title": "Черновик"}},
                ],
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://sheets.googleapis.com/v4",
    ) as http_client:
        gateway = GoogleServiceAccountSheetsGateway(GoogleSheetsSettings(), http_client)
        gateway._access_token = AsyncMock(return_value="test-token")  # type: ignore[method-assign]
        result = await gateway.check_connection("spreadsheet-123")

    assert result.spreadsheet_title == "Портфель заказчика"
    assert result.worksheet_names == ("Доход счёт 2026", "Черновик")


async def test_google_gateway_reads_sparse_input_columns_as_unformatted_values() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        row = ["RU000A107SX3", "", "", 46167, "", "", 2, 962.9, "", "", 0.4]
        return httpx.Response(200, json={"values": [row]})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://sheets.googleapis.com/v4",
    ) as http_client:
        gateway = GoogleServiceAccountSheetsGateway(GoogleSheetsSettings(), http_client)
        gateway._access_token = AsyncMock(return_value="test-token")  # type: ignore[method-assign]
        rows = await gateway.read_rows(
            "spreadsheet-123",
            "Доход счёт 2026",
            ("B", "E", "H", "I", "J", "L"),
            3,
        )

    assert len(rows) == 1
    assert rows[0].row_number == 3
    assert rows[0].values == {
        "B": "RU000A107SX3",
        "E": 46167,
        "H": 2,
        "I": 962.9,
        "J": "",
        "L": 0.4,
    }
    assert captured[0].url.params["valueRenderOption"] == "UNFORMATTED_VALUE"


async def test_google_gateway_explains_missing_service_account_key(tmp_path: Path) -> None:
    settings = GoogleSheetsSettings(
        credentials_file=tmp_path / "missing-service-account.json",
        retries=1,
    )
    gateway = GoogleServiceAccountSheetsGateway(settings)
    try:
        with pytest.raises(GoogleSheetsError, match="Не найден JSON-ключ"):
            await gateway.check_connection("spreadsheet-123")
    finally:
        await gateway.close()


async def test_google_gateway_explains_forbidden_access() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(403, json={"error": {"message": "forbidden"}})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://sheets.googleapis.com/v4",
    ) as http_client:
        gateway = GoogleServiceAccountSheetsGateway(
            GoogleSheetsSettings(retries=1),
            http_client,
        )
        gateway._access_token = AsyncMock(return_value="test-token")  # type: ignore[method-assign]
        with pytest.raises(GoogleSheetsError, match="права Editor"):
            await gateway.check_connection("spreadsheet-123")
