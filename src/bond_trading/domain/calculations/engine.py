from datetime import date
from decimal import Decimal

from bond_trading.domain.calculations.models import (
    ActionType,
    CorporateCashFlow,
    CurrentYieldInput,
    PlannedYieldInput,
    PurchaseInput,
    PurchaseResult,
    TaxPolicy,
    YieldResult,
)
from bond_trading.domain.calculations.tax import apply_tax
from bond_trading.domain.errors import InvalidAmountError, InvalidHoldingPeriodError

ZERO = Decimal(0)


def calculate_purchase(value: PurchaseInput) -> PurchaseResult:
    _validate_purchase(value)
    clean_total = value.clean_price_rub_per_bond * value.quantity
    accrued_total = value.accrued_interest_rub_per_bond * value.quantity
    commission_total = value.commission_rub_per_bond * value.quantity
    return PurchaseResult(
        clean_price_total=clean_total,
        accrued_interest_total=accrued_total,
        commission_total=commission_total,
        purchase_total=clean_total + accrued_total + commission_total,
    )


def annualize_profit(
    profit: Decimal,
    purchase_total: Decimal,
    purchase_date: date,
    exit_date: date,
) -> tuple[Decimal, int]:
    holding_days = (exit_date - purchase_date).days
    if holding_days <= 0:
        raise InvalidHoldingPeriodError(
            "Exit date must be after purchase date",
            {"purchase_date": purchase_date, "exit_date": exit_date},
        )
    if purchase_total <= 0:
        raise InvalidAmountError("Purchase total must be positive", purchase_total)
    annual_yield = profit / purchase_total * Decimal(365) / Decimal(holding_days) * Decimal(100)
    return annual_yield, holding_days


def calculate_planned_yield(value: PlannedYieldInput) -> YieldResult:
    if value.final_redemption_rub_per_bond < 0 or value.sale_commission_rub_per_bond < 0:
        raise InvalidAmountError("Exit amounts cannot be negative")
    purchase = calculate_purchase(value.purchase)
    coupons, amortizations = _cashflow_totals(
        value.cashflows,
        value.purchase.purchase_date,
        value.target_date,
        value.purchase.quantity,
    )
    redemption_total = value.final_redemption_rub_per_bond * value.purchase.quantity
    sale_commission = value.sale_commission_rub_per_bond * value.purchase.quantity
    exit_total = coupons + amortizations + redemption_total - sale_commission
    return _yield_result(
        purchase=purchase,
        coupons=coupons,
        amortizations=amortizations,
        redemption_or_market=redemption_total,
        accrued_interest=ZERO,
        sale_commission=sale_commission,
        exit_total=exit_total,
        purchase_date=value.purchase.purchase_date,
        exit_date=value.target_date,
        tax_policy=value.tax_policy,
    )


def calculate_current_yield(value: CurrentYieldInput) -> YieldResult:
    for amount in (
        value.bid_rub_per_bond,
        value.current_accrued_interest_rub_per_bond,
        value.sale_commission_rub_per_bond,
    ):
        if amount < 0:
            raise InvalidAmountError("Current exit amounts cannot be negative", amount)
    purchase = calculate_purchase(value.purchase)
    coupons, amortizations = _cashflow_totals(
        value.cashflows,
        value.purchase.purchase_date,
        value.valuation_date,
        value.purchase.quantity,
    )
    market_total = value.bid_rub_per_bond * value.purchase.quantity
    accrued_total = value.current_accrued_interest_rub_per_bond * value.purchase.quantity
    sale_commission = value.sale_commission_rub_per_bond * value.purchase.quantity
    exit_total = market_total + accrued_total + coupons + amortizations - sale_commission
    return _yield_result(
        purchase=purchase,
        coupons=coupons,
        amortizations=amortizations,
        redemption_or_market=market_total,
        accrued_interest=accrued_total,
        sale_commission=sale_commission,
        exit_total=exit_total,
        purchase_date=value.purchase.purchase_date,
        exit_date=value.valuation_date,
        tax_policy=value.tax_policy,
    )


def _cashflow_totals(
    cashflows: tuple[CorporateCashFlow, ...],
    purchase_date: date,
    exit_date: date,
    quantity: Decimal,
) -> tuple[Decimal, Decimal]:
    coupons = ZERO
    amortizations = ZERO
    seen: set[tuple[ActionType, date, Decimal]] = set()
    for cashflow in cashflows:
        if cashflow.amount_rub_per_bond < 0:
            raise InvalidAmountError("Cash flow amount cannot be negative", cashflow)
        if not purchase_date < cashflow.event_date <= exit_date:
            continue
        identity = (cashflow.action_type, cashflow.event_date, cashflow.amount_rub_per_bond)
        if identity in seen:
            continue
        seen.add(identity)
        if cashflow.action_type is ActionType.COUPON:
            coupons += cashflow.amount_rub_per_bond * quantity
        elif cashflow.action_type is ActionType.AMORTIZATION:
            amortizations += cashflow.amount_rub_per_bond * quantity
    return coupons, amortizations


def _yield_result(
    *,
    purchase: PurchaseResult,
    coupons: Decimal,
    amortizations: Decimal,
    redemption_or_market: Decimal,
    accrued_interest: Decimal,
    sale_commission: Decimal,
    exit_total: Decimal,
    purchase_date: date,
    exit_date: date,
    tax_policy: TaxPolicy,
) -> YieldResult:
    profit_before_tax = exit_total - purchase.purchase_total
    profit_after_tax = apply_tax(profit_before_tax, tax_policy)
    annual_before, holding_days = annualize_profit(
        profit_before_tax, purchase.purchase_total, purchase_date, exit_date
    )
    annual_after, _ = annualize_profit(
        profit_after_tax, purchase.purchase_total, purchase_date, exit_date
    )
    return YieldResult(
        purchase=purchase,
        coupons_total=coupons,
        amortizations_total=amortizations,
        redemption_or_market_total=redemption_or_market,
        accrued_interest_total=accrued_interest,
        sale_commission_total=sale_commission,
        exit_total=exit_total,
        profit_before_tax=profit_before_tax,
        profit_after_tax=profit_after_tax,
        annual_yield_before_tax=annual_before,
        annual_yield_after_tax=annual_after,
        holding_days=holding_days,
    )


def _validate_purchase(value: PurchaseInput) -> None:
    if value.quantity <= 0:
        raise InvalidAmountError("Quantity must be positive", value.quantity)
    if any(
        amount < 0
        for amount in (
            value.clean_price_rub_per_bond,
            value.accrued_interest_rub_per_bond,
            value.commission_rub_per_bond,
        )
    ):
        raise InvalidAmountError("Purchase amounts cannot be negative")
