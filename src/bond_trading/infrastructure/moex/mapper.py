import hashlib
import json
import logging
from collections.abc import Mapping
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from bond_trading.domain.calculations import ActionType, QuoteBasis, bid_to_rubles
from bond_trading.domain.value_objects import normalize_isin
from bond_trading.infrastructure.moex.errors import MoexDataError, MoexNotFoundError
from bond_trading.infrastructure.moex.schemas import (
    MoexCorporateActionData,
    MoexInstrumentData,
    MoexMarketData,
    MoexRefreshResult,
)
from bond_trading.infrastructure.moex.tables import first_row, table_rows

logger = logging.getLogger(__name__)


def find_exact_security(payload: Mapping[str, Any], isin: str) -> dict[str, Any]:
    normalized = normalize_isin(isin)
    matches = [
        row
        for row in table_rows(payload, "securities")
        if str(row.get("isin") or "").strip().upper() == normalized
    ]
    if not matches:
        raise MoexNotFoundError(f"MOEX instrument {normalized} was not found")
    traded = [row for row in matches if row.get("is_traded") == 1]
    return traded[0] if traded else matches[0]


def map_refresh_result(
    *,
    isin: str,
    search_payload: Mapping[str, Any],
    specification_payload: Mapping[str, Any],
    market_payload: Mapping[str, Any],
    bondization_payload: Mapping[str, Any],
    timezone: ZoneInfo,
    received_at: datetime | None = None,
) -> MoexRefreshResult:
    now = received_at or datetime.now(UTC)
    search_row = find_exact_security(search_payload, isin)
    security_row = first_row(market_payload, "securities") or {}
    market_row = first_row(market_payload, "marketdata") or {}
    descriptions = _description_values(specification_payload)
    actions = _map_actions(bondization_payload)

    secid = str(search_row.get("secid") or descriptions.get("SECID") or "")
    if not secid:
        raise MoexDataError("MOEX search result has no SECID")
    current_face = _decimal(security_row.get("FACEVALUE"))
    if current_face is None:
        current_face = _decimal(descriptions.get("FACEVALUE"))
    initial_face = _initial_face_value(bondization_payload) or current_face
    currency = _currency(security_row.get("FACEUNIT") or descriptions.get("FACEUNIT"))
    maturity_date = _date(security_row.get("MATDATE") or descriptions.get("MATDATE"))
    offer_date = _first_date(
        security_row.get("OFFERDATE"),
        security_row.get("PUTOPTIONDATE"),
        security_row.get("BUYBACKDATE"),
    )
    short_name = str(search_row.get("shortname") or security_row.get("SHORTNAME") or secid).strip()
    full_name_raw = (
        search_row.get("name") or security_row.get("SECNAME") or descriptions.get("NAME")
    )
    full_name = str(full_name_raw).strip() if full_name_raw else None
    bond_type = f"{security_row.get('BONDTYPE') or ''} {security_row.get('BONDSUBTYPE') or ''}"
    source_updated_at = _market_datetime(market_row.get("SYSTIME"), timezone) or now
    instrument = MoexInstrumentData(
        isin=normalize_isin(isin),
        secid=secid,
        short_name=short_name,
        full_name=full_name,
        primary_board_id=_optional_string(
            search_row.get("primary_boardid") or security_row.get("BOARDID")
        ),
        currency=currency,
        initial_face_value=initial_face,
        current_face_value=current_face,
        maturity_date=maturity_date,
        offer_date=offer_date,
        coupon_period_days=_integer(security_row.get("COUPONPERIOD")),
        coupon_value=_decimal(security_row.get("COUPONVALUE")),
        is_amortizing="Амортиз" in bond_type,
        is_floating_coupon="Флоатер" in bond_type,
        is_active=search_row.get("is_traded") == 1,
        source_updated_at=source_updated_at,
    )

    bid_percent = _decimal(market_row.get("BID"))
    bid_rub = (
        bid_to_rubles(bid_percent, current_face, QuoteBasis.PERCENT_OF_FACE)
        if bid_percent is not None and current_face is not None
        else None
    )
    if "BIDDEPTH" not in market_row and market_row:
        logger.warning(
            "MOEX market data is missing optional field",
            extra={"source": "MOEX ISS", "reason": "missing BIDDEPTH", "isin": isin},
        )
    market_timestamp = _market_datetime(market_row.get("SYSTIME"), timezone)
    status = "ok" if bid_percent is not None else "no_bid"
    if not security_row and not market_row:
        status = "no_market_data"
    market = MoexMarketData(
        board_id=_optional_string(security_row.get("BOARDID") or search_row.get("primary_boardid")),
        received_at=now,
        market_timestamp=market_timestamp,
        bid_percent=bid_percent,
        bid_rub_per_bond=bid_rub,
        bid_depth_lots=_decimal(market_row.get("BIDDEPTH")),
        lot_size=_decimal(security_row.get("LOTSIZE")) or Decimal(1),
        current_face_value=current_face,
        accrued_interest_rub_per_bond=_decimal(security_row.get("ACCRUEDINT")),
        last_price_percent=_decimal(market_row.get("LAST")),
        status=status,
        delayed_status="unknown",
        raw_payload={
            "securities": market_payload.get("securities", {}),
            "marketdata": market_payload.get("marketdata", {}),
            "dataversion": market_payload.get("dataversion", {}),
        },
    )
    return MoexRefreshResult(instrument=instrument, actions=actions, market=market)


def _map_actions(payload: Mapping[str, Any]) -> tuple[MoexCorporateActionData, ...]:
    actions: list[MoexCorporateActionData] = []
    for row in table_rows(payload, "coupons"):
        event_date = _date(row.get("coupondate"))
        if event_date is not None:
            actions.append(_action(ActionType.COUPON, event_date, row, "recorddate"))
    for row in table_rows(payload, "amortizations"):
        event_date = _date(row.get("amortdate"))
        if event_date is not None:
            action_type = (
                ActionType.MATURITY
                if str(row.get("data_source") or "").lower() == "maturity"
                else ActionType.AMORTIZATION
            )
            actions.append(_action(action_type, event_date, row))
    for row in table_rows(payload, "offers"):
        event_date = _date(row.get("offerdate"))
        if event_date is not None:
            actions.append(_action(ActionType.OFFER, event_date, row))
    return tuple(sorted(actions, key=lambda action: (action.event_date, action.action_type)))


def _action(
    action_type: ActionType,
    event_date: date,
    row: Mapping[str, Any],
    record_date_field: str | None = None,
) -> MoexCorporateActionData:
    amount = _decimal(row.get("value_rub"))
    if amount is None:
        amount = _decimal(row.get("value"))
    return MoexCorporateActionData(
        action_type=action_type,
        event_date=event_date,
        record_date=_date(row.get(record_date_field)) if record_date_field else None,
        amount_rub_per_bond=amount,
        percent=_decimal(row.get("valueprc")),
        source_payload_hash=hashlib.sha256(
            json.dumps(dict(row), sort_keys=True, ensure_ascii=False, default=str).encode()
        ).hexdigest(),
    )


def _description_values(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        str(row.get("name")): row.get("value")
        for row in table_rows(payload, "description")
        if row.get("name")
    }


def _initial_face_value(payload: Mapping[str, Any]) -> Decimal | None:
    for block in ("coupons", "amortizations"):
        for row in table_rows(payload, block):
            value = _decimal(row.get("initialfacevalue"))
            if value is not None:
                return value
    return None


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation as exc:
        raise MoexDataError(f"Invalid decimal value from MOEX: {value!r}") from exc


def _integer(value: Any) -> int | None:
    decimal = _decimal(value)
    return int(decimal) if decimal is not None else None


def _date(value: Any) -> date | None:
    if value in (None, "", "0000-00-00"):
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise MoexDataError(f"Invalid date value from MOEX: {value!r}") from exc


def _first_date(*values: Any) -> date | None:
    for value in values:
        parsed = _date(value)
        if parsed is not None:
            return parsed
    return None


def _market_datetime(value: Any, timezone: ZoneInfo) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError as exc:
        raise MoexDataError(f"Invalid timestamp value from MOEX: {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone)
    return parsed.astimezone(UTC)


def _currency(value: Any) -> str:
    raw = str(value or "RUB").upper()
    return "RUB" if raw in {"RUB", "SUR"} else raw


def _optional_string(value: Any) -> str | None:
    return str(value).strip() if value not in (None, "") else None
