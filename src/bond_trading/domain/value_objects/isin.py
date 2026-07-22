import re

from bond_trading.domain.errors import InvalidIsinError

ISIN_PATTERN = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")


def normalize_isin(value: str) -> str:
    normalized = value.strip().upper()
    validate_isin(normalized)
    return normalized


def validate_isin(value: str, *, check_digit: bool = True) -> None:
    if not ISIN_PATTERN.fullmatch(value):
        raise InvalidIsinError("ISIN must contain 12 valid uppercase characters", value)
    if check_digit and not _valid_check_digit(value):
        raise InvalidIsinError("ISIN check digit is invalid", value)


def _valid_check_digit(value: str) -> bool:
    expanded = "".join(str(ord(char) - 55) if char.isalpha() else char for char in value)
    total = 0
    should_double = False
    for digit_text in reversed(expanded):
        digit = int(digit_text)
        if should_double:
            digit *= 2
            digit = digit // 10 + digit % 10
        total += digit
        should_double = not should_double
    return total % 10 == 0
