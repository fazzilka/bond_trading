from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum


class ActionType(StrEnum):
    COUPON = "coupon"
    AMORTIZATION = "amortization"
    OFFER = "offer"
    MATURITY = "maturity"


class TaxMode(StrEnum):
    NONE = "none"
    FLAT_RATE = "flat_rate"
    LEGACY_DIVIDE_1_13 = "legacy_divide_1_13"


class QuoteBasis(StrEnum):
    PERCENT_OF_FACE = "percent_of_face"
    RUBLES = "rubles"


class LiquidityStatus(StrEnum):
    SUFFICIENT = "sufficient"
    PARTIAL = "partial"
    NONE = "none"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class TaxPolicy:
    mode: TaxMode = TaxMode.NONE
    rate: Decimal = Decimal("0.13")


@dataclass(frozen=True, slots=True)
class CorporateCashFlow:
    action_type: ActionType
    event_date: date
    amount_rub_per_bond: Decimal


@dataclass(frozen=True, slots=True)
class PurchaseInput:
    purchase_date: date
    quantity: Decimal
    clean_price_rub_per_bond: Decimal
    accrued_interest_rub_per_bond: Decimal
    commission_rub_per_bond: Decimal


@dataclass(frozen=True, slots=True)
class PlannedYieldInput:
    purchase: PurchaseInput
    target_date: date
    final_redemption_rub_per_bond: Decimal
    cashflows: tuple[CorporateCashFlow, ...] = field(default_factory=tuple)
    sale_commission_rub_per_bond: Decimal = Decimal(0)
    tax_policy: TaxPolicy = TaxPolicy()


@dataclass(frozen=True, slots=True)
class CurrentYieldInput:
    purchase: PurchaseInput
    valuation_date: date
    bid_rub_per_bond: Decimal
    current_accrued_interest_rub_per_bond: Decimal
    cashflows: tuple[CorporateCashFlow, ...] = field(default_factory=tuple)
    sale_commission_rub_per_bond: Decimal = Decimal(0)
    tax_policy: TaxPolicy = TaxPolicy()


@dataclass(frozen=True, slots=True)
class PurchaseResult:
    clean_price_total: Decimal
    accrued_interest_total: Decimal
    commission_total: Decimal
    purchase_total: Decimal


@dataclass(frozen=True, slots=True)
class YieldResult:
    purchase: PurchaseResult
    coupons_total: Decimal
    amortizations_total: Decimal
    redemption_or_market_total: Decimal
    accrued_interest_total: Decimal
    sale_commission_total: Decimal
    exit_total: Decimal
    profit_before_tax: Decimal
    profit_after_tax: Decimal
    annual_yield_before_tax: Decimal
    annual_yield_after_tax: Decimal
    holding_days: int


@dataclass(frozen=True, slots=True)
class LiquidityResult:
    status: LiquidityStatus
    available_bonds_at_best_bid: Decimal | None
