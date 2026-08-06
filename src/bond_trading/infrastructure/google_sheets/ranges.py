import re

_SPREADSHEET_URL_RE = re.compile(r"/spreadsheets/d/([A-Za-z0-9_-]+)")
_SPREADSHEET_ID_RE = re.compile(r"^[A-Za-z0-9_-]{10,255}$")
_COLUMN_RE = re.compile(r"^[A-Za-z]{1,3}$")


def extract_spreadsheet_id(value: str) -> str:
    candidate = value.strip()
    match = _SPREADSHEET_URL_RE.search(candidate)
    if match:
        candidate = match.group(1)
    if not _SPREADSHEET_ID_RE.fullmatch(candidate):
        raise ValueError("Не удалось определить ID Google Таблицы")
    return candidate


def normalize_column(value: str | None, *, required: bool = True) -> str | None:
    if value is None or not value.strip():
        if required:
            raise ValueError("Не указана обязательная колонка Google Таблицы")
        return None
    candidate = value.strip().upper()
    if not _COLUMN_RE.fullmatch(candidate) or column_number(candidate) > 16_384:
        raise ValueError(f"Некорректная колонка Google Таблицы: {value}")
    return candidate


def quote_worksheet(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def column_number(value: str) -> int:
    result = 0
    for character in value:
        result = result * 26 + ord(character) - ord("A") + 1
    return result


def column_name(value: int) -> str:
    if not 1 <= value <= 16_384:
        raise ValueError(f"Некорректный номер колонки Google Таблицы: {value}")
    result = ""
    current = value
    while current:
        current, remainder = divmod(current - 1, 26)
        result = chr(ord("A") + remainder) + result
    return result
