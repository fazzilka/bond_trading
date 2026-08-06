from datetime import UTC, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from bond_trading.domain.calculations import ActionType
from bond_trading.infrastructure.moex.errors import MoexDataError, MoexNotFoundError
from bond_trading.infrastructure.moex.mapper import map_refresh_result


def block(columns: list[str], *rows: list[object]) -> dict[str, object]:
    return {"columns": columns, "data": list(rows)}


def payloads() -> tuple[dict[str, object], ...]:
    search = {
        "securities": block(
            ["name", "isin", "primary_boardid", "is_traded", "shortname", "secid"],
            ["Other", "RU000A000000", "TQCB", 1, "Other", "OTHER"],
            ["ЭкономЛизинг", "RU000A107SX3", "TQCB", 1, "ЭконЛиз1Р7", "RU000A107SX3"],
        )
    }
    specification = {
        "description": block(
            ["value", "name"],
            ["1000", "FACEVALUE"],
            ["RUB", "FACEUNIT"],
        )
    }
    market = {
        "securities": block(
            [
                "BONDTYPE",
                "BONDSUBTYPE",
                "COUPONVALUE",
                "COUPONPERIOD",
                "MATDATE",
                "FACEUNIT",
                "FACEVALUE",
                "LOTSIZE",
                "ACCRUEDINT",
                "BOARDID",
            ],
            [
                "Фикс с известным купоном",
                "До погашения",
                39.89,
                91,
                "2027-02-15",
                "SUR",
                1000,
                1,
                0,
                "TQCB",
            ],
        ),
        "marketdata": block(
            ["LAST", "BIDDEPTH", "SYSTIME", "BID", "OFFER", "OFFERDEPTH"],
            [97.16, 12, "2026-07-22 15:39:25", 97.23, 97.43, 8],
        ),
    }
    bondization = {
        "coupons": block(
            ["coupondate", "recorddate", "value_rub", "valueprc", "initialfacevalue"],
            ["2026-08-17", "2026-08-14", 39.89, 16, 1000],
        ),
        "amortizations": block(
            ["amortdate", "value_rub", "valueprc", "data_source"],
            ["2027-02-15", 1000, 100, "maturity"],
        ),
        "offers": block(["offerdate", "value"], []),
    }
    return search, specification, market, bondization


def test_map_complete_response_with_changed_column_order() -> None:
    search, specification, market, bondization = payloads()
    result = map_refresh_result(
        isin="ru000a107sx3",
        search_payload=search,
        specification_payload=specification,
        market_payload=market,
        bondization_payload=bondization,
        timezone=ZoneInfo("Europe/Moscow"),
        received_at=datetime(2026, 7, 22, tzinfo=UTC),
    )

    assert result.instrument.isin == "RU000A107SX3"
    assert result.instrument.current_face_value == Decimal("1000")
    assert result.instrument.coupon_value == Decimal("39.89")
    assert result.market.bid_percent == Decimal("97.23")
    assert result.market.bid_rub_per_bond == Decimal("972.30")
    assert result.market.bid_depth_lots == Decimal("12")
    assert result.market.offer_percent == Decimal("97.43")
    assert result.market.offer_rub_per_bond == Decimal("974.30")
    assert result.market.offer_depth_lots == Decimal("8")
    assert result.market.accrued_interest_rub_per_bond == Decimal("0")
    assert result.market.market_timestamp == datetime(2026, 7, 22, 12, 39, 25, tzinfo=UTC)
    assert [action.action_type for action in result.actions] == [
        ActionType.COUPON,
        ActionType.MATURITY,
    ]


def test_missing_bid_is_not_replaced_by_last() -> None:
    search, specification, market, bondization = payloads()
    market["marketdata"] = block(["LAST", "BID", "BIDDEPTH"], [99.5, None, None])

    result = map_refresh_result(
        isin="RU000A107SX3",
        search_payload=search,
        specification_payload=specification,
        market_payload=market,
        bondization_payload=bondization,
        timezone=ZoneInfo("Europe/Moscow"),
    )

    assert result.market.status == "no_bid"
    assert result.market.bid_percent is None
    assert result.market.bid_rub_per_bond is None
    assert result.market.last_price_percent == Decimal("99.5")


def test_missing_offer_is_not_replaced_by_last_or_bid() -> None:
    search, specification, market, bondization = payloads()
    market["marketdata"] = block(
        ["LAST", "BID", "OFFER", "OFFERDEPTH"],
        [99.5, 99.4, None, None],
    )

    result = map_refresh_result(
        isin="RU000A107SX3",
        search_payload=search,
        specification_payload=specification,
        market_payload=market,
        bondization_payload=bondization,
        timezone=ZoneInfo("Europe/Moscow"),
    )

    assert result.market.bid_rub_per_bond == Decimal("994.0")
    assert result.market.offer_percent is None
    assert result.market.offer_rub_per_bond is None
    assert result.market.last_price_percent == Decimal("99.5")


def test_null_accrued_interest_and_non_default_board_are_preserved() -> None:
    search, specification, market, bondization = payloads()
    search["securities"] = block(
        ["isin", "secid", "shortname", "is_traded", "primary_boardid"],
        ["RU000A107SX3", "RU000A107SX3", "Bond", 1, "TQOD"],
    )
    securities = market["securities"]
    assert isinstance(securities, dict)
    accrued_index = securities["columns"].index("ACCRUEDINT")
    board_index = securities["columns"].index("BOARDID")
    securities["data"][0][accrued_index] = None
    securities["data"][0][board_index] = "TQOD"

    result = map_refresh_result(
        isin="RU000A107SX3",
        search_payload=search,
        specification_payload=specification,
        market_payload=market,
        bondization_payload=bondization,
        timezone=ZoneInfo("Europe/Moscow"),
    )

    assert result.instrument.primary_board_id == "TQOD"
    assert result.market.board_id == "TQOD"
    assert result.market.accrued_interest_rub_per_bond is None


def test_inactive_security_has_no_market_data() -> None:
    search, specification, _, bondization = payloads()
    search["securities"] = block(
        ["isin", "secid", "shortname", "is_traded", "primary_boardid"],
        ["RU000A107SX3", "RU000A107SX3", "Bond", 0, "TQCB"],
    )
    result = map_refresh_result(
        isin="RU000A107SX3",
        search_payload=search,
        specification_payload=specification,
        market_payload={"securities": block([], []), "marketdata": block([], [])},
        bondization_payload=bondization,
        timezone=ZoneInfo("Europe/Moscow"),
    )

    assert result.instrument.is_active is False
    assert result.market.status == "no_market_data"
    assert result.market.bid_rub_per_bond is None


def test_not_found_and_invalid_values() -> None:
    search, specification, market, bondization = payloads()
    search["securities"] = block(["isin"], ["RU000A000000"])
    with pytest.raises(MoexNotFoundError):
        map_refresh_result(
            isin="RU000A107SX3",
            search_payload=search,
            specification_payload=specification,
            market_payload=market,
            bondization_payload=bondization,
            timezone=ZoneInfo("Europe/Moscow"),
        )

    search, specification, market, bondization = payloads()
    market["securities"] = block(["FACEVALUE"], ["not-a-number"])
    with pytest.raises(MoexDataError):
        map_refresh_result(
            isin="RU000A107SX3",
            search_payload=search,
            specification_payload=specification,
            market_payload=market,
            bondization_payload=bondization,
            timezone=ZoneInfo("Europe/Moscow"),
        )
