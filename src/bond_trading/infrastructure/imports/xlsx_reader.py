import hashlib
import io
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, cast

import openpyxl
from openpyxl.cell.cell import Cell
from openpyxl.worksheet.worksheet import Worksheet

from bond_trading.domain.errors import InvalidIsinError
from bond_trading.domain.value_objects import normalize_isin

DEFAULT_SHEET_NAME = "Доход счёт 2026"
ALLOWED_XLSX_MEDIA_TYPES = frozenset(
    {
        "application/octet-stream",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/x-zip-compressed",
        "application/zip",
    }
)


class XlsxImportError(ValueError):
    pass


def validate_xlsx_upload(file_name: str, content_type: str | None) -> None:
    if Path(file_name).suffix.lower() != ".xlsx":
        raise XlsxImportError("Only .xlsx files are supported")
    normalized_content_type = (content_type or "").partition(";")[0].strip().lower()
    if normalized_content_type and normalized_content_type not in ALLOWED_XLSX_MEDIA_TYPES:
        raise XlsxImportError("The upload MIME type is not valid for an XLSX workbook")


@dataclass(frozen=True, slots=True)
class ImportRowError:
    row_number: int
    field: str
    message: str
    source_value: str | None = None


@dataclass(frozen=True, slots=True)
class ImportRow:
    row_number: int
    source_isin: str
    normalized_isin: str
    source_name: str | None
    target_event_type: str
    purchase_date: date
    target_event_date: date
    quantity: Decimal
    purchase_clean_price_rub_per_bond: Decimal
    purchase_accrued_interest_rub_per_bond: Decimal
    purchase_commission_rub_per_bond: Decimal
    target_redemption_price_rub_per_bond: Decimal | None
    sale_commission_rub_per_bond: Decimal
    planned_yield_manual_reference: Decimal | None
    offer_submission_period: str | None


@dataclass(frozen=True, slots=True)
class ImportPreview:
    file_name: str
    sheet_name: str
    checksum: str
    header_row_number: int
    rows_read: int
    rows: tuple[ImportRow, ...]
    errors: tuple[ImportRowError, ...]


class XlsxPortfolioReader:
    def preview(
        self,
        content: bytes,
        file_name: str,
        *,
        sheet_name: str = DEFAULT_SHEET_NAME,
    ) -> ImportPreview:
        if Path(file_name).suffix.lower() != ".xlsx":
            raise XlsxImportError("Only .xlsx files are supported")
        try:
            workbook = openpyxl.load_workbook(io.BytesIO(content), data_only=False, read_only=False)
        except Exception as exc:
            raise XlsxImportError("The uploaded file is not a readable XLSX workbook") from exc
        worksheet = self._select_sheet(workbook.worksheets, sheet_name)
        header_row, isin_column = self._find_header(worksheet)
        rows: list[ImportRow] = []
        errors: list[ImportRowError] = []
        rows_read = 0
        for row_number in range(header_row + 1, worksheet.max_row + 1):
            cells = list(worksheet[row_number])
            if self._is_empty_row(cells):
                break
            rows_read += 1
            try:
                rows.append(self._parse_row(worksheet, row_number, isin_column))
            except _RowValidationError as exc:
                errors.append(
                    ImportRowError(
                        row_number=row_number,
                        field=exc.field,
                        message=exc.message,
                        source_value=exc.source_value,
                    )
                )
        return ImportPreview(
            file_name=Path(file_name).name,
            sheet_name=worksheet.title,
            checksum=hashlib.sha256(content).hexdigest(),
            header_row_number=header_row,
            rows_read=rows_read,
            rows=tuple(rows),
            errors=tuple(errors),
        )

    def _select_sheet(self, worksheets: Sequence[Any], requested: str) -> Worksheet:
        by_name = {sheet.title.strip(): sheet for sheet in worksheets}
        if requested in by_name:
            return cast(Worksheet, by_name[requested])
        for worksheet in worksheets:
            try:
                self._find_header(worksheet)
            except XlsxImportError:
                continue
            return cast(Worksheet, worksheet)
        raise XlsxImportError(
            f"Sheet {requested!r} or another sheet with an ISIN header was not found"
        )

    def _find_header(self, worksheet: Worksheet) -> tuple[int, int]:
        for row in worksheet.iter_rows():
            for cell in row:
                if str(cell.value or "").strip().upper() == "ISIN":
                    return cast(int, cell.row), cast(int, cell.column)
        raise XlsxImportError(f"ISIN header was not found on sheet {worksheet.title!r}")

    def _parse_row(self, worksheet: Worksheet, row: int, isin_column: int) -> ImportRow:
        def cell(column: int) -> Cell:
            return cast(Cell, worksheet.cell(row=row, column=column))

        source_isin = _manual_text(cell(isin_column), "isin", required=True)
        assert source_isin is not None
        try:
            normalized_isin = normalize_isin(source_isin)
        except InvalidIsinError as exc:
            raise _RowValidationError("isin", exc.message, source_isin) from exc
        target_event = _event_type(_manual_text(cell(4), "target_event_type", required=True))
        return ImportRow(
            row_number=row,
            source_isin=source_isin,
            normalized_isin=normalized_isin,
            source_name=_text(cell(3).value),
            target_event_type=target_event,
            purchase_date=_manual_date(cell(5), "purchase_date"),
            target_event_date=_manual_date(cell(7), "target_event_date"),
            quantity=_manual_decimal(cell(8), "quantity", positive=True),
            purchase_clean_price_rub_per_bond=_manual_decimal(
                cell(9), "purchase_clean_price_rub_per_bond", non_negative=True
            ),
            purchase_accrued_interest_rub_per_bond=_manual_decimal(
                cell(10), "purchase_accrued_interest_rub_per_bond", non_negative=True
            ),
            purchase_commission_rub_per_bond=_manual_decimal(
                cell(12), "purchase_commission_rub_per_bond", non_negative=True
            ),
            target_redemption_price_rub_per_bond=_optional_decimal(cell(14)),
            sale_commission_rub_per_bond=_optional_decimal(cell(18)) or Decimal(0),
            planned_yield_manual_reference=_optional_decimal(cell(22)),
            offer_submission_period=_text(cell(6).value),
        )

    @staticmethod
    def _is_empty_row(cells: Sequence[Any]) -> bool:
        return all(cell.value is None or str(cell.value).strip() == "" for cell in cells)


class _RowValidationError(ValueError):
    def __init__(self, field: str, message: str, source_value: str | None = None) -> None:
        super().__init__(message)
        self.field = field
        self.message = message
        self.source_value = source_value


def _reject_formula(cell: Cell, field: str) -> None:
    if cell.data_type == "f" or (isinstance(cell.value, str) and cell.value.startswith("=")):
        raise _RowValidationError(
            field, "A manual source field cannot contain a formula", str(cell.value)
        )


def _manual_text(cell: Cell, field: str, *, required: bool) -> str | None:
    _reject_formula(cell, field)
    value = _text(cell.value)
    if required and value is None:
        raise _RowValidationError(field, "A required value is missing")
    return value


def _manual_date(cell: Cell, field: str) -> date:
    _reject_formula(cell, field)
    value = cell.value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip())
        except ValueError as exc:
            raise _RowValidationError(field, "Date must use YYYY-MM-DD", value) from exc
    raise _RowValidationError(field, "A valid date is required", _text(value))


def _manual_decimal(
    cell: Cell,
    field: str,
    *,
    positive: bool = False,
    non_negative: bool = False,
) -> Decimal:
    _reject_formula(cell, field)
    value = _decimal(cell.value, field)
    if positive and value <= 0:
        raise _RowValidationError(field, "Value must be positive", str(cell.value))
    if non_negative and value < 0:
        raise _RowValidationError(field, "Value cannot be negative", str(cell.value))
    return value


def _optional_decimal(cell: Cell) -> Decimal | None:
    if cell.value in (None, ""):
        return None
    if cell.data_type == "f":
        return None
    return _decimal(cell.value, "optional_decimal")


def _decimal(value: Any, field: str) -> Decimal:
    if value in (None, ""):
        raise _RowValidationError(field, "A numeric value is required")
    try:
        return Decimal(str(value).replace(" ", "").replace(",", "."))
    except InvalidOperation as exc:
        raise _RowValidationError(field, "A valid decimal value is required", str(value)) from exc


def _event_type(value: str | None) -> str:
    normalized = (value or "").strip().lower()
    if normalized in {"погашение", "maturity"}:
        return "maturity"
    if normalized in {"оферта", "offer"}:
        return "offer"
    raise _RowValidationError(
        "target_event_type", "Event type must be maturity/погашение or offer/оферта", value
    )


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
