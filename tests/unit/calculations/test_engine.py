from datetime import date
from decimal import Decimal

import pytest

from bond_trading.domain.calculations import (
    ActionType,
    CorporateCashFlow,
    CurrentYieldInput,
    PlannedYieldInput,
    PurchaseInput,
    TaxMode,
    TaxPolicy,
    annualize_profit,
    calculate_current_yield,
    calculate_planned_yield,
    calculate_purchase,
)
from bond_trading.domain.errors import InvalidAmountError, InvalidHoldingPeriodError

D = Decimal


@pytest.fixture
def purchase() -> PurchaseInput:
    return PurchaseInput(
        purchase_date=date(2026, 5, 25),
        quantity=D("40"),
        clean_price_rub_per_bond=D("962.90"),
        accrued_interest_rub_per_bond=D("3.51"),
        commission_rub_per_bond=D("0.39"),
    )


def test_purchase_commission_is_per_bond(purchase: PurchaseInput) -> None:
    result = calculate_purchase(purchase)

    assert result.clean_price_total == D("38516.00")
    assert result.accrued_interest_total == D("140.40")
    assert result.commission_total == D("15.60")
    assert result.purchase_total == D("38672.00")


def test_regression_legacy_purchase_total(purchase: PurchaseInput) -> None:
    corrected = calculate_purchase(purchase).purchase_total
    legacy = purchase.clean_price_rub_per_bond * purchase.quantity + D("140.40") + D("0.39")

    assert legacy == D("38656.79")
    assert corrected == D("38672.00")
    assert corrected - legacy == D("15.21")


def test_ru000a107sx3_planned_yield(purchase: PurchaseInput) -> None:
    coupons = tuple(
        CorporateCashFlow(ActionType.COUPON, event_date, D("39.89"))
        for event_date in (date(2026, 8, 17), date(2026, 11, 16), date(2027, 2, 15))
    )
    result = calculate_planned_yield(
        PlannedYieldInput(
            purchase=purchase,
            target_date=date(2027, 2, 15),
            final_redemption_rub_per_bond=D("1000"),
            cashflows=coupons,
            tax_policy=TaxPolicy(TaxMode.LEGACY_DIVIDE_1_13),
        )
    )

    assert result.purchase.purchase_total == D("38672.00")
    assert result.coupons_total == D("4786.80")
    assert result.exit_total == D("44786.80")
    assert result.profit_before_tax == D("6114.80")
    assert result.holding_days == 266
    assert result.annual_yield_after_tax == pytest.approx(D("19.2008"), abs=D("0.0001"))


def test_ru000a107sx3_current_yield(purchase: PurchaseInput) -> None:
    result = calculate_current_yield(
        CurrentYieldInput(
            purchase=purchase,
            valuation_date=date(2026, 9, 1),
            bid_rub_per_bond=D("982"),
            current_accrued_interest_rub_per_bond=D("3.20"),
            cashflows=(CorporateCashFlow(ActionType.COUPON, date(2026, 8, 17), D("39.89")),),
            tax_policy=TaxPolicy(TaxMode.LEGACY_DIVIDE_1_13),
        )
    )

    assert result.coupons_total == D("1595.60")
    assert result.accrued_interest_total == D("128.00")
    assert result.exit_total == D("41003.60")
    assert result.profit_before_tax == D("2331.60")
    assert result.holding_days == 99
    assert result.annual_yield_after_tax == pytest.approx(D("19.6715"), abs=D("0.0001"))


def test_cashflow_boundaries_and_duplicate_protection(purchase: PurchaseInput) -> None:
    cashflow = CorporateCashFlow(ActionType.COUPON, date(2026, 8, 17), D("10"))
    result = calculate_current_yield(
        CurrentYieldInput(
            purchase=purchase,
            valuation_date=date(2026, 8, 17),
            bid_rub_per_bond=D("1000"),
            current_accrued_interest_rub_per_bond=D("0"),
            cashflows=(
                CorporateCashFlow(ActionType.COUPON, date(2026, 5, 25), D("99")),
                cashflow,
                cashflow,
                CorporateCashFlow(ActionType.COUPON, date(2026, 8, 18), D("99")),
            ),
        )
    )

    assert result.coupons_total == D("400")


def test_amortization_is_counted_without_maturity_duplication(purchase: PurchaseInput) -> None:
    result = calculate_planned_yield(
        PlannedYieldInput(
            purchase=purchase,
            target_date=date(2027, 2, 15),
            final_redemption_rub_per_bond=D("600"),
            cashflows=(
                CorporateCashFlow(ActionType.AMORTIZATION, date(2026, 11, 16), D("400")),
                CorporateCashFlow(ActionType.MATURITY, date(2027, 2, 15), D("600")),
            ),
        )
    )

    assert result.amortizations_total == D("16000")
    assert result.redemption_or_market_total == D("24000")


def test_sale_commission_reduces_exit(purchase: PurchaseInput) -> None:
    result = calculate_current_yield(
        CurrentYieldInput(
            purchase=purchase,
            valuation_date=date(2026, 9, 1),
            bid_rub_per_bond=D("1000"),
            current_accrued_interest_rub_per_bond=D("0"),
            sale_commission_rub_per_bond=D("0.50"),
        )
    )

    assert result.sale_commission_total == D("20.00")
    assert result.exit_total == D("39980.00")


@pytest.mark.parametrize(
    "purchase_date, exit_date",
    [(date(2026, 1, 1), date(2026, 1, 1)), (date(2026, 1, 2), date(2026, 1, 1))],
)
def test_invalid_holding_period(purchase_date: date, exit_date: date) -> None:
    with pytest.raises(InvalidHoldingPeriodError):
        annualize_profit(D("1"), D("100"), purchase_date, exit_date)


def test_invalid_purchase_values(purchase: PurchaseInput) -> None:
    with pytest.raises(InvalidAmountError):
        calculate_purchase(
            PurchaseInput(
                purchase.purchase_date,
                D("0"),
                purchase.clean_price_rub_per_bond,
                purchase.accrued_interest_rub_per_bond,
                purchase.commission_rub_per_bond,
            )
        )

    with pytest.raises(InvalidAmountError):
        calculate_purchase(
            PurchaseInput(
                purchase.purchase_date,
                purchase.quantity,
                D("-1"),
                purchase.accrued_interest_rub_per_bond,
                purchase.commission_rub_per_bond,
            )
        )


def test_invalid_exit_values(purchase: PurchaseInput) -> None:
    with pytest.raises(InvalidAmountError):
        calculate_planned_yield(
            PlannedYieldInput(
                purchase=purchase,
                target_date=date(2027, 1, 1),
                final_redemption_rub_per_bond=D("-1"),
            )
        )
    with pytest.raises(InvalidAmountError):
        calculate_current_yield(
            CurrentYieldInput(
                purchase=purchase,
                valuation_date=date(2026, 9, 1),
                bid_rub_per_bond=D("-1"),
                current_accrued_interest_rub_per_bond=D("0"),
            )
        )


def test_annualization_requires_positive_purchase_total() -> None:
    with pytest.raises(InvalidAmountError):
        annualize_profit(D("1"), D("0"), date(2026, 1, 1), date(2026, 1, 2))


def test_reject_negative_cashflow(purchase: PurchaseInput) -> None:
    with pytest.raises(InvalidAmountError):
        calculate_planned_yield(
            PlannedYieldInput(
                purchase=purchase,
                target_date=date(2027, 1, 1),
                final_redemption_rub_per_bond=D("1000"),
                cashflows=(CorporateCashFlow(ActionType.COUPON, date(2026, 8, 1), D("-1")),),
            )
        )
