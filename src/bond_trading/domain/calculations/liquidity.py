from decimal import Decimal

from bond_trading.domain.calculations.models import LiquidityResult, LiquidityStatus
from bond_trading.domain.errors import InvalidAmountError


def evaluate_liquidity(
    *,
    quantity: Decimal,
    bid_present: bool,
    bid_depth_lots: Decimal | None,
    lot_size: Decimal,
) -> LiquidityResult:
    if quantity <= 0 or lot_size <= 0:
        raise InvalidAmountError("Quantity and lot size must be positive")
    if not bid_present:
        return LiquidityResult(LiquidityStatus.NONE, Decimal(0))
    if bid_depth_lots is None:
        return LiquidityResult(LiquidityStatus.UNKNOWN, None)
    available = bid_depth_lots * lot_size
    if available <= 0:
        return LiquidityResult(LiquidityStatus.NONE, available)
    status = LiquidityStatus.SUFFICIENT if available >= quantity else LiquidityStatus.PARTIAL
    return LiquidityResult(status, available)
