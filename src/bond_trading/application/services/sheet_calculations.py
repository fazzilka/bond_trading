from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from bond_trading.domain.calculations import (
    ActionType,
    CorporateCashFlow,
    CurrentYieldInput,
    PurchaseInput,
    TaxMode,
    TaxPolicy,
    calculate_current_yield,
)

DEFAULT_PURCHASE_COMMISSION = Decimal("0.4")

PURCHASE_DATE_COLUMN = "E"
QUANTITY_COLUMN = "H"
PURCHASE_PRICE_COLUMN = "I"
PURCHASE_ACCRUED_COLUMN = "J"
PURCHASE_COMMISSION_COLUMN = "L"
PLANNED_ANNUAL_YIELD_COLUMN = "V"

SHEET_INPUT_COLUMNS = (
    PURCHASE_DATE_COLUMN,
    QUANTITY_COLUMN,
    PURCHASE_PRICE_COLUMN,
    PURCHASE_ACCRUED_COLUMN,
    PURCHASE_COMMISSION_COLUMN,
    PLANNED_ANNUAL_YIELD_COLUMN,
)

CALCULATION_HEADERS = {
    "AD": "Текущий НКД MOEX, ₽/шт.",
    "AE": "Выплачено купонов, шт.",
    "AF": "Получено купонов, ₽",
    "AG": "Текущая стоимость с купонами, ₽",
    "AH": "Текущий доход до налога, ₽",
    "AI": "Текущий чистый доход, ₽",
    "AJ": "Годовой текущий %, после налога",
    "AK": "Отклонение от плана, п.п.",
    "AL": "Статус расчёта",
}

_CUSTOMER_TAX_POLICY = TaxPolicy(TaxMode.LEGACY_DIVIDE_1_13, Decimal("0.13"))
_SHEETS_EPOCH = date(1899, 12, 30)


@dataclass(frozen=True, slots=True)
class SheetPurchaseInput:
    purchase_date: date
    quantity: Decimal
    purchase_price_rub_per_bond: Decimal
    purchase_accrued_rub_per_bond: Decimal
    purchase_commission_total_rub: Decimal
    planned_annual_yield_percent: Decimal | None


@dataclass(frozen=True, slots=True)
class SheetCurrentCalculation:
    paid_coupon_count: int
    paid_coupons_total_rub: Decimal
    current_exit_total_rub: Decimal
    profit_before_tax_rub: Decimal
    profit_after_tax_rub: Decimal
    annual_yield_after_tax_percent: Decimal
    plan_delta_pp: Decimal | None


def summarize_paid_coupons(
    *,
    purchase_date: date,
    valuation_date: date,
    quantity: Decimal,
    cashflows: tuple[CorporateCashFlow, ...],
) -> tuple[int, Decimal]:
    paid = tuple(
        cashflow
        for cashflow in cashflows
        if cashflow.action_type is ActionType.COUPON
        and purchase_date < cashflow.event_date <= valuation_date
    )
    return len({cashflow.event_date for cashflow in paid}), sum(
        (cashflow.amount_rub_per_bond * quantity for cashflow in paid),
        start=Decimal(0),
    )


def parse_sheet_purchase(
    values: dict[str, object],
    *,
    purchase_accrued: Decimal | None,
    commission_total: Decimal,
) -> tuple[SheetPurchaseInput | None, str | None]:
    missing: list[str] = []
    purchase_date = parse_date(values.get(PURCHASE_DATE_COLUMN))
    quantity = parse_decimal(values.get(QUANTITY_COLUMN))
    purchase_price = parse_decimal(values.get(PURCHASE_PRICE_COLUMN))
    if purchase_date is None:
        missing.append(PURCHASE_DATE_COLUMN)
    if quantity is None:
        missing.append(QUANTITY_COLUMN)
    if purchase_price is None:
        missing.append(PURCHASE_PRICE_COLUMN)
    if missing:
        return None, f"НЕТ ДАННЫХ: {', '.join(missing)}"
    if purchase_accrued is None:
        return None, "НКД НА ДАТУ ПОКУПКИ НЕ НАЙДЕН"
    assert purchase_date is not None
    assert quantity is not None
    assert purchase_price is not None
    if quantity <= 0:
        return None, "КОЛИЧЕСТВО ДОЛЖНО БЫТЬ БОЛЬШЕ НУЛЯ"
    if purchase_price < 0 or purchase_accrued < 0 or commission_total < 0:
        return None, "ЦЕНА, НКД И КОМИССИЯ НЕ МОГУТ БЫТЬ ОТРИЦАТЕЛЬНЫМИ"
    return (
        SheetPurchaseInput(
            purchase_date=purchase_date,
            quantity=quantity,
            purchase_price_rub_per_bond=purchase_price,
            purchase_accrued_rub_per_bond=purchase_accrued,
            purchase_commission_total_rub=commission_total,
            planned_annual_yield_percent=parse_decimal(values.get(PLANNED_ANNUAL_YIELD_COLUMN)),
        ),
        None,
    )


def calculate_sheet_current(
    purchase: SheetPurchaseInput,
    *,
    valuation_date: date,
    offer_rub_per_bond: Decimal,
    current_accrued_rub_per_bond: Decimal,
    cashflows: tuple[CorporateCashFlow, ...],
) -> SheetCurrentCalculation:
    result = calculate_current_yield(
        CurrentYieldInput(
            purchase=PurchaseInput(
                purchase_date=purchase.purchase_date,
                quantity=purchase.quantity,
                clean_price_rub_per_bond=purchase.purchase_price_rub_per_bond,
                accrued_interest_rub_per_bond=purchase.purchase_accrued_rub_per_bond,
                # L is the total commission for the row; the domain DTO stores it per bond.
                commission_rub_per_bond=(
                    purchase.purchase_commission_total_rub / purchase.quantity
                ),
            ),
            valuation_date=valuation_date,
            bid_rub_per_bond=offer_rub_per_bond,
            current_accrued_interest_rub_per_bond=current_accrued_rub_per_bond,
            cashflows=cashflows,
            tax_policy=_CUSTOMER_TAX_POLICY,
        )
    )
    paid_coupon_count, _ = summarize_paid_coupons(
        purchase_date=purchase.purchase_date,
        valuation_date=valuation_date,
        quantity=purchase.quantity,
        cashflows=cashflows,
    )
    plan_delta = (
        result.annual_yield_after_tax - purchase.planned_annual_yield_percent
        if purchase.planned_annual_yield_percent is not None
        else None
    )
    return SheetCurrentCalculation(
        paid_coupon_count=paid_coupon_count,
        paid_coupons_total_rub=result.coupons_total,
        current_exit_total_rub=result.exit_total,
        profit_before_tax_rub=result.profit_before_tax,
        profit_after_tax_rub=result.profit_after_tax,
        annual_yield_after_tax_percent=result.annual_yield_after_tax,
        plan_delta_pp=plan_delta,
    )


def parse_decimal(value: object) -> Decimal | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    candidate = str(value).strip().replace("\u00a0", "").replace(" ", "").replace(",", ".")
    if not candidate:
        return None
    try:
        return Decimal(candidate)
    except InvalidOperation:
        return None


def parse_date(value: object) -> date | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, int | float | Decimal):
        try:
            return _SHEETS_EPOCH + timedelta(days=int(Decimal(str(value))))
        except (InvalidOperation, OverflowError):
            return None
    candidate = str(value).strip()
    for pattern in ("%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y", "%d.%m.%Y", "%d.%m.%y"):
        try:
            return datetime.strptime(candidate, pattern).date()
        except ValueError:
            continue
    return None
