from decimal import Decimal

import pytest

from bond_trading.domain.calculations import (
    LiquidityStatus,
    QuoteBasis,
    TaxMode,
    TaxPolicy,
    bid_to_rubles,
    evaluate_liquidity,
)
from bond_trading.domain.calculations.tax import apply_tax
from bond_trading.domain.errors import InvalidAmountError

D = Decimal


@pytest.mark.parametrize(
    "policy, expected",
    [
        (TaxPolicy(TaxMode.NONE), D("100")),
        (TaxPolicy(TaxMode.FLAT_RATE, D("0.13")), D("87.00")),
        (TaxPolicy(TaxMode.LEGACY_DIVIDE_1_13), D("100") / D("1.13")),
    ],
)
def test_tax_strategies(policy: TaxPolicy, expected: Decimal) -> None:
    assert apply_tax(D("100"), policy) == expected


def test_tax_does_not_increase_loss() -> None:
    assert apply_tax(D("-100"), TaxPolicy(TaxMode.FLAT_RATE, D("0.13"))) == D("-100")


def test_invalid_tax_rate() -> None:
    with pytest.raises(InvalidAmountError):
        apply_tax(D("100"), TaxPolicy(TaxMode.FLAT_RATE, D("1.1")))

    with pytest.raises(InvalidAmountError):
        apply_tax(D("100"), TaxPolicy("unsupported"))  # type: ignore[arg-type]


def test_bid_percent_conversion() -> None:
    assert bid_to_rubles(D("97.23"), D("1000"), QuoteBasis.PERCENT_OF_FACE) == D("972.30")
    assert bid_to_rubles(D("100.3"), D("900"), QuoteBasis.PERCENT_OF_FACE) == D("902.7")


def test_ruble_bid_is_not_converted() -> None:
    assert bid_to_rubles(D("982"), D("1000"), QuoteBasis.RUBLES) == D("982")

    with pytest.raises(InvalidAmountError):
        bid_to_rubles(D("-1"), D("1000"), QuoteBasis.RUBLES)

    with pytest.raises(InvalidAmountError):
        bid_to_rubles(D("1"), D("1000"), "unsupported")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "bid_present, depth, expected_status, expected_available",
    [
        (False, None, LiquidityStatus.NONE, D("0")),
        (True, None, LiquidityStatus.UNKNOWN, None),
        (True, D("0"), LiquidityStatus.NONE, D("0")),
        (True, D("10"), LiquidityStatus.PARTIAL, D("10")),
        (True, D("40"), LiquidityStatus.SUFFICIENT, D("40")),
        (True, D("50"), LiquidityStatus.SUFFICIENT, D("50")),
    ],
)
def test_liquidity_status(
    bid_present: bool,
    depth: Decimal | None,
    expected_status: LiquidityStatus,
    expected_available: Decimal | None,
) -> None:
    result = evaluate_liquidity(
        quantity=D("40"), bid_present=bid_present, bid_depth_lots=depth, lot_size=D("1")
    )

    assert result.status is expected_status
    assert result.available_bonds_at_best_bid == expected_available


def test_liquidity_rejects_invalid_quantity() -> None:
    with pytest.raises(InvalidAmountError):
        evaluate_liquidity(
            quantity=D("0"), bid_present=True, bid_depth_lots=D("1"), lot_size=D("1")
        )
