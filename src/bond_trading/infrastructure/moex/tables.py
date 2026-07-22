from collections.abc import Mapping
from typing import Any

from bond_trading.infrastructure.moex.errors import MoexDataError


def table_rows(payload: Mapping[str, Any], block: str) -> list[dict[str, Any]]:
    table = payload.get(block)
    if table is None:
        return []
    if not isinstance(table, Mapping):
        raise MoexDataError(f"MOEX block {block!r} is not an object")
    columns = table.get("columns")
    data = table.get("data")
    if not isinstance(columns, list) or not isinstance(data, list):
        raise MoexDataError(f"MOEX block {block!r} has no columns/data arrays")
    rows: list[dict[str, Any]] = []
    for raw_row in data:
        if not isinstance(raw_row, list):
            raise MoexDataError(f"MOEX block {block!r} contains a non-array row")
        rows.append(dict(zip(columns, raw_row, strict=False)))
    return rows


def first_row(payload: Mapping[str, Any], block: str) -> dict[str, Any] | None:
    rows = table_rows(payload, block)
    return rows[0] if rows else None
