import pytest

from bond_trading.infrastructure.google_sheets import (
    extract_spreadsheet_id,
    normalize_column,
)


def test_extract_spreadsheet_id_from_url_or_raw_value() -> None:
    spreadsheet_id = "1AbCdEfGhIjKlMnOpQrStUvWxYz"

    assert extract_spreadsheet_id(spreadsheet_id) == spreadsheet_id
    assert (
        extract_spreadsheet_id(
            f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit#gid=123"
        )
        == spreadsheet_id
    )


def test_reject_invalid_spreadsheet_id_and_columns() -> None:
    with pytest.raises(ValueError, match="ID Google Таблицы"):
        extract_spreadsheet_id("https://example.com/not-a-sheet")
    with pytest.raises(ValueError, match="Некорректная колонка"):
        normalize_column("XFE")

    assert normalize_column(" x ") == "X"
    assert normalize_column("", required=False) is None
