from decimal import Decimal

from bond_trading.domain.calculations.models import QuoteBasis
from bond_trading.domain.errors import InvalidAmountError


def bid_to_rubles(bid: Decimal, face_value: Decimal, quote_basis: QuoteBasis) -> Decimal:
    if bid < 0 or face_value < 0:
        raise InvalidAmountError("Bid and face value cannot be negative")
    if quote_basis is QuoteBasis.PERCENT_OF_FACE:
        return bid / Decimal(100) * face_value
    if quote_basis is QuoteBasis.RUBLES:
        return bid
    raise InvalidAmountError("Unsupported quote basis", quote_basis)
