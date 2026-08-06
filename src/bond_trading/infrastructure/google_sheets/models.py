from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol


@dataclass(frozen=True, slots=True)
class SheetIsinRow:
    row_number: int
    raw_isin: str


@dataclass(frozen=True, slots=True)
class SheetDataRow:
    row_number: int
    values: dict[str, object]


@dataclass(frozen=True, slots=True)
class CellUpdate:
    row_number: int
    column: str
    value: Decimal | str | None


@dataclass(frozen=True, slots=True)
class SheetConnectionCheck:
    spreadsheet_title: str
    worksheet_names: tuple[str, ...]


class GoogleSheetsGateway(Protocol):
    async def check_connection(self, spreadsheet_id: str) -> SheetConnectionCheck: ...

    async def read_isins(
        self,
        spreadsheet_id: str,
        worksheet_name: str,
        isin_column: str,
        first_data_row: int,
    ) -> list[SheetIsinRow]: ...

    async def read_rows(
        self,
        spreadsheet_id: str,
        worksheet_name: str,
        columns: tuple[str, ...],
        first_data_row: int,
    ) -> list[SheetDataRow]: ...

    async def update_cells(
        self,
        spreadsheet_id: str,
        worksheet_name: str,
        updates: list[CellUpdate],
    ) -> int: ...
