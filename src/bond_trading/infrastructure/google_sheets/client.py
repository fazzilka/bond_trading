import asyncio
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.service_account import Credentials

from bond_trading.core.config import GoogleSheetsSettings
from bond_trading.infrastructure.google_sheets.models import (
    CellUpdate,
    SheetConnectionCheck,
    SheetDataRow,
    SheetIsinRow,
)
from bond_trading.infrastructure.google_sheets.ranges import (
    column_name,
    column_number,
    normalize_column,
    quote_worksheet,
)

_SCOPE = "https://www.googleapis.com/auth/spreadsheets"


class GoogleSheetsError(RuntimeError):
    pass


class GoogleServiceAccountSheetsGateway:
    def __init__(
        self,
        settings: GoogleSheetsSettings,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self._http = http_client or httpx.AsyncClient(
            base_url=settings.api_base_url,
            timeout=settings.timeout_seconds,
        )
        self._owns_http_client = http_client is None
        self._credentials: Credentials | None = None
        self._credential_lock = asyncio.Lock()

    async def close(self) -> None:
        if self._owns_http_client:
            await self._http.aclose()

    async def check_connection(self, spreadsheet_id: str) -> SheetConnectionCheck:
        response = await self._request(
            "GET",
            f"/spreadsheets/{spreadsheet_id}",
            params={"fields": "properties.title,sheets.properties.title"},
        )
        payload = self._json_object(response)
        properties = payload.get("properties")
        sheets = payload.get("sheets")
        title = properties.get("title") if isinstance(properties, dict) else None
        names: list[str] = []
        if isinstance(sheets, list):
            for sheet in sheets:
                sheet_properties = sheet.get("properties") if isinstance(sheet, dict) else None
                sheet_title = (
                    sheet_properties.get("title") if isinstance(sheet_properties, dict) else None
                )
                if isinstance(sheet_title, str):
                    names.append(sheet_title)
        if not isinstance(title, str):
            raise GoogleSheetsError("Google API не вернул название таблицы")
        return SheetConnectionCheck(title, tuple(names))

    async def read_isins(
        self,
        spreadsheet_id: str,
        worksheet_name: str,
        isin_column: str,
        first_data_row: int,
    ) -> list[SheetIsinRow]:
        cell_range = (
            f"{quote_worksheet(worksheet_name)}!{isin_column}{first_data_row}:{isin_column}"
        )
        response = await self._request(
            "GET",
            f"/spreadsheets/{spreadsheet_id}/values/{quote(cell_range, safe='')}",
            params={"majorDimension": "ROWS", "valueRenderOption": "FORMULA"},
        )
        payload = self._json_object(response)
        raw_values = payload.get("values", [])
        if not isinstance(raw_values, list):
            raise GoogleSheetsError("Google API вернул некорректный диапазон ISIN")
        rows: list[SheetIsinRow] = []
        for offset, raw_row in enumerate(raw_values):
            if not isinstance(raw_row, list) or not raw_row:
                continue
            value = str(raw_row[0]).strip()
            if value:
                rows.append(SheetIsinRow(first_data_row + offset, value))
        return rows

    async def read_rows(
        self,
        spreadsheet_id: str,
        worksheet_name: str,
        columns: tuple[str, ...],
        first_data_row: int,
    ) -> list[SheetDataRow]:
        normalized = tuple(str(normalize_column(column)) for column in columns)
        first_column_number = min(column_number(column) for column in normalized)
        last_column_number = max(column_number(column) for column in normalized)
        first_column = column_name(first_column_number)
        last_column = column_name(last_column_number)
        cell_range = (
            f"{quote_worksheet(worksheet_name)}!{first_column}{first_data_row}:{last_column}"
        )
        response = await self._request(
            "GET",
            f"/spreadsheets/{spreadsheet_id}/values/{quote(cell_range, safe='')}",
            params={
                "majorDimension": "ROWS",
                "valueRenderOption": "UNFORMATTED_VALUE",
                "dateTimeRenderOption": "SERIAL_NUMBER",
            },
        )
        payload = self._json_object(response)
        raw_values = payload.get("values", [])
        if not isinstance(raw_values, list):
            raise GoogleSheetsError("Google API вернул некорректный диапазон строк")
        rows: list[SheetDataRow] = []
        for offset, raw_row in enumerate(raw_values):
            if not isinstance(raw_row, list):
                continue
            values = {
                column: raw_row[column_number(column) - first_column_number]
                for column in normalized
                if column_number(column) - first_column_number < len(raw_row)
            }
            if any(value not in (None, "") for value in values.values()):
                rows.append(SheetDataRow(first_data_row + offset, values))
        return rows

    async def update_cells(
        self,
        spreadsheet_id: str,
        worksheet_name: str,
        updates: list[CellUpdate],
    ) -> int:
        worksheet = quote_worksheet(worksheet_name)
        data = [
            {
                "range": f"{worksheet}!{update.column}{update.row_number}",
                "majorDimension": "ROWS",
                "values": [[_json_value(update.value)]],
            }
            for update in updates
        ]
        if not data:
            return 0
        response = await self._request(
            "POST",
            f"/spreadsheets/{spreadsheet_id}/values:batchUpdate",
            json={"valueInputOption": "RAW", "data": data},
        )
        payload = self._json_object(response)
        updated_cells = payload.get("totalUpdatedCells")
        return int(updated_cells) if isinstance(updated_cells, int | float) else len(updates)

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(self._settings.retries):
            try:
                token = await self._access_token(force_refresh=attempt > 0)
                response = await self._http.request(
                    method,
                    path,
                    headers={"Authorization": f"Bearer {token}"},
                    **kwargs,
                )
                if response.status_code in {429, 500, 502, 503, 504}:
                    raise GoogleSheetsError(
                        f"Временная ошибка Google Sheets: HTTP {response.status_code}"
                    )
                if response.status_code in {401, 403}:
                    raise GoogleSheetsError(
                        "Google Sheets отклонил доступ. Проверьте service account и права Editor"
                    )
                response.raise_for_status()
                return response
            except (httpx.TransportError, GoogleSheetsError) as exc:
                last_error = exc
                if attempt + 1 >= self._settings.retries:
                    break
                await asyncio.sleep(min(2**attempt, 4))
        raise GoogleSheetsError(str(last_error or "Неизвестная ошибка Google Sheets"))

    async def _access_token(self, *, force_refresh: bool) -> str:
        async with self._credential_lock:
            if self._credentials is None:
                path = Path(self._settings.credentials_file)
                if not path.is_file():
                    raise GoogleSheetsError(f"Не найден JSON-ключ Google service account: {path}")
                self._credentials = await asyncio.to_thread(
                    Credentials.from_service_account_file,
                    str(path),
                    scopes=[_SCOPE],
                )
            credentials = self._credentials
            if credentials is None:
                raise GoogleSheetsError("Не удалось загрузить Google credentials")
            if force_refresh or not credentials.valid or not credentials.token:
                await asyncio.to_thread(credentials.refresh, GoogleAuthRequest())
            if not credentials.token:
                raise GoogleSheetsError("Google не выдал access token")
            return str(credentials.token)

    @staticmethod
    def _json_object(response: httpx.Response) -> dict[str, Any]:
        payload = response.json()
        if not isinstance(payload, dict):
            raise GoogleSheetsError("Google API вернул некорректный JSON")
        return payload


class UnavailableGoogleSheetsGateway:
    def __init__(self, message: str) -> None:
        self._message = message

    async def check_connection(self, spreadsheet_id: str) -> SheetConnectionCheck:
        del spreadsheet_id
        raise GoogleSheetsError(self._message)

    async def read_isins(
        self,
        spreadsheet_id: str,
        worksheet_name: str,
        isin_column: str,
        first_data_row: int,
    ) -> list[SheetIsinRow]:
        del spreadsheet_id, worksheet_name, isin_column, first_data_row
        raise GoogleSheetsError(self._message)

    async def read_rows(
        self,
        spreadsheet_id: str,
        worksheet_name: str,
        columns: tuple[str, ...],
        first_data_row: int,
    ) -> list[SheetDataRow]:
        del spreadsheet_id, worksheet_name, columns, first_data_row
        raise GoogleSheetsError(self._message)

    async def update_cells(
        self,
        spreadsheet_id: str,
        worksheet_name: str,
        updates: list[CellUpdate],
    ) -> int:
        del spreadsheet_id, worksheet_name, updates
        raise GoogleSheetsError(self._message)


class MemoryGoogleSheetsGateway:
    def __init__(self) -> None:
        self.spreadsheets: dict[str, tuple[str, set[str]]] = {}
        self.cells: dict[tuple[str, str, int, str], object] = {}
        self.update_batches: list[list[CellUpdate]] = []

    def add_spreadsheet(
        self,
        spreadsheet_id: str,
        *,
        title: str,
        worksheets: tuple[str, ...],
    ) -> None:
        self.spreadsheets[spreadsheet_id] = (title, set(worksheets))

    def set_cell(
        self,
        spreadsheet_id: str,
        worksheet_name: str,
        row_number: int,
        column: str,
        value: object,
    ) -> None:
        self.cells[(spreadsheet_id, worksheet_name, row_number, column)] = value

    async def check_connection(self, spreadsheet_id: str) -> SheetConnectionCheck:
        spreadsheet = self.spreadsheets.get(spreadsheet_id)
        if spreadsheet is None:
            raise GoogleSheetsError("Тестовая Google Таблица не найдена")
        title, worksheets = spreadsheet
        return SheetConnectionCheck(title, tuple(sorted(worksheets)))

    async def read_isins(
        self,
        spreadsheet_id: str,
        worksheet_name: str,
        isin_column: str,
        first_data_row: int,
    ) -> list[SheetIsinRow]:
        await self._require_worksheet(spreadsheet_id, worksheet_name)
        values = [
            (row, value)
            for (book, sheet, row, column), value in self.cells.items()
            if book == spreadsheet_id
            and sheet == worksheet_name
            and column == isin_column
            and row >= first_data_row
            and str(value).strip()
        ]
        return [SheetIsinRow(row, str(value)) for row, value in sorted(values)]

    async def read_rows(
        self,
        spreadsheet_id: str,
        worksheet_name: str,
        columns: tuple[str, ...],
        first_data_row: int,
    ) -> list[SheetDataRow]:
        await self._require_worksheet(spreadsheet_id, worksheet_name)
        row_numbers = sorted(
            {
                row
                for book, sheet, row, column in self.cells
                if book == spreadsheet_id
                and sheet == worksheet_name
                and column in columns
                and row >= first_data_row
            }
        )
        return [
            SheetDataRow(
                row,
                {
                    column: self.cells[(spreadsheet_id, worksheet_name, row, column)]
                    for column in columns
                    if (spreadsheet_id, worksheet_name, row, column) in self.cells
                },
            )
            for row in row_numbers
        ]

    async def update_cells(
        self,
        spreadsheet_id: str,
        worksheet_name: str,
        updates: list[CellUpdate],
    ) -> int:
        await self._require_worksheet(spreadsheet_id, worksheet_name)
        self.update_batches.append(list(updates))
        for update in updates:
            self.set_cell(
                spreadsheet_id,
                worksheet_name,
                update.row_number,
                update.column,
                "" if update.value is None else update.value,
            )
        return len(updates)

    async def _require_worksheet(self, spreadsheet_id: str, worksheet_name: str) -> None:
        check = await self.check_connection(spreadsheet_id)
        if worksheet_name not in check.worksheet_names:
            raise GoogleSheetsError(f"Тестовая вкладка «{worksheet_name}» не найдена")


def _json_value(value: object) -> object:
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return float(value)  # Decimal is serialized as a numeric Google Sheets cell.
    return value
