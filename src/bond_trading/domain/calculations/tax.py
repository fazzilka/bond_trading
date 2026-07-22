from decimal import Decimal

from bond_trading.domain.calculations.models import TaxMode, TaxPolicy
from bond_trading.domain.errors import InvalidAmountError


def apply_tax(profit: Decimal, policy: TaxPolicy) -> Decimal:
    if profit <= 0 or policy.mode is TaxMode.NONE:
        return profit
    if policy.mode is TaxMode.FLAT_RATE:
        if not Decimal(0) <= policy.rate <= Decimal(1):
            raise InvalidAmountError("Tax rate must be between zero and one", policy.rate)
        return profit * (Decimal(1) - policy.rate)
    if policy.mode is TaxMode.LEGACY_DIVIDE_1_13:
        return profit / Decimal("1.13")
    raise InvalidAmountError("Unsupported tax mode", policy.mode)
