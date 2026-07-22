import pytest

from bond_trading.domain.errors import InvalidIsinError
from bond_trading.domain.value_objects import normalize_isin


@pytest.mark.parametrize(
    "raw, expected",
    [
        (" RU000A107sg8 ", "RU000A107SG8"),
        ("RU000A107SX3", "RU000A107SX3"),
        ("RU000A10ASF9", "RU000A10ASF9"),
    ],
)
def test_normalize_valid_isin(raw: str, expected: str) -> None:
    assert normalize_isin(raw) == expected


@pytest.mark.parametrize("value", ["", "RU000A107SX4", "RU000A107SX", "RU000A107SX!"])
def test_reject_invalid_isin(value: str) -> None:
    with pytest.raises(InvalidIsinError):
        normalize_isin(value)
