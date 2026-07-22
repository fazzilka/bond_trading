from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from bond_trading.domain.calculations.models import ActionType


@dataclass(frozen=True, slots=True)
class MoexInstrumentData:
    isin: str
    secid: str
    short_name: str
    full_name: str | None
    primary_board_id: str | None
    currency: str
    initial_face_value: Decimal | None
    current_face_value: Decimal | None
    maturity_date: date | None
    offer_date: date | None
    coupon_period_days: int | None
    coupon_value: Decimal | None
    is_amortizing: bool
    is_floating_coupon: bool
    is_active: bool
    source_updated_at: datetime


@dataclass(frozen=True, slots=True)
class MoexCorporateActionData:
    action_type: ActionType
    event_date: date
    record_date: date | None
    amount_rub_per_bond: Decimal | None
    percent: Decimal | None
    source_payload_hash: str


@dataclass(frozen=True, slots=True)
class MoexMarketData:
    board_id: str | None
    received_at: datetime
    market_timestamp: datetime | None
    bid_percent: Decimal | None
    bid_rub_per_bond: Decimal | None
    bid_depth_lots: Decimal | None
    lot_size: Decimal
    current_face_value: Decimal | None
    accrued_interest_rub_per_bond: Decimal | None
    last_price_percent: Decimal | None
    status: str
    delayed_status: str
    raw_payload: dict[str, object]


@dataclass(frozen=True, slots=True)
class MoexRefreshResult:
    instrument: MoexInstrumentData
    actions: tuple[MoexCorporateActionData, ...]
    market: MoexMarketData
