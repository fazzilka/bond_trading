from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from bond_trading.domain.calculations.models import TaxMode
from bond_trading.infrastructure.db.models import BondLotModel


class InstrumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
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
    is_amortizing: bool
    is_floating_coupon: bool
    is_active: bool
    source_updated_at: datetime | None


class MarketSnapshotOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
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
    error_message: str | None


class InstrumentRefreshOut(BaseModel):
    instrument: InstrumentOut
    market: MarketSnapshotOut


class LotCreate(BaseModel):
    isin: str = Field(examples=["RU000A107SX3"])
    source_name: str | None = None
    purchase_date: date
    quantity: Decimal = Field(gt=0)
    purchase_clean_price_rub_per_bond: Decimal = Field(ge=0)
    purchase_accrued_interest_rub_per_bond: Decimal = Field(ge=0)
    purchase_commission_rub_per_bond: Decimal = Field(ge=0)
    target_event_type: Literal["maturity", "offer"]
    target_event_date: date
    target_redemption_price_rub_per_bond: Decimal | None = Field(default=None, ge=0)
    sale_commission_rub_per_bond: Decimal = Field(default=Decimal(0), ge=0)
    planned_yield_manual_reference: Decimal | None = None
    source_row_number: int | None = None
    source_sheet_name: str = "manual"
    notes: str | None = None


class LotPatch(BaseModel):
    purchase_date: date | None = None
    quantity: Decimal | None = Field(default=None, gt=0)
    purchase_clean_price_rub_per_bond: Decimal | None = Field(default=None, ge=0)
    purchase_accrued_interest_rub_per_bond: Decimal | None = Field(default=None, ge=0)
    purchase_commission_rub_per_bond: Decimal | None = Field(default=None, ge=0)
    target_event_type: Literal["maturity", "offer"] | None = None
    target_event_date: date | None = None
    target_redemption_price_rub_per_bond: Decimal | None = Field(default=None, ge=0)
    sale_commission_rub_per_bond: Decimal | None = Field(default=None, ge=0)
    planned_yield_manual_reference: Decimal | None = None
    notes: str | None = None


class LotOut(BaseModel):
    id: UUID
    instrument_id: UUID
    isin: str
    short_name: str
    purchase_date: date
    quantity: Decimal
    purchase_clean_price_rub_per_bond: Decimal
    purchase_accrued_interest_rub_per_bond: Decimal
    purchase_commission_rub_per_bond: Decimal
    target_event_type: str
    target_event_date: date
    target_redemption_price_rub_per_bond: Decimal | None
    sale_commission_rub_per_bond: Decimal
    planned_yield_manual_reference: Decimal | None
    source_row_number: int | None
    source_sheet_name: str
    notes: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, lot: BondLotModel) -> Self:
        return cls(
            id=lot.id,
            instrument_id=lot.instrument_id,
            isin=lot.instrument.isin,
            short_name=lot.instrument.short_name,
            purchase_date=lot.purchase_date,
            quantity=lot.quantity,
            purchase_clean_price_rub_per_bond=(lot.purchase_clean_price_rub_per_bond),
            purchase_accrued_interest_rub_per_bond=(lot.purchase_accrued_interest_rub_per_bond),
            purchase_commission_rub_per_bond=(lot.purchase_commission_rub_per_bond),
            target_event_type=lot.target_event_type,
            target_event_date=lot.target_event_date,
            target_redemption_price_rub_per_bond=(lot.target_redemption_price_rub_per_bond),
            sale_commission_rub_per_bond=lot.sale_commission_rub_per_bond,
            planned_yield_manual_reference=lot.planned_yield_manual_reference,
            source_row_number=lot.source_row_number,
            source_sheet_name=lot.source_sheet_name,
            notes=lot.notes,
            created_at=lot.created_at,
            updated_at=lot.updated_at,
        )


class CalculateRequest(BaseModel):
    valuation_date: date | None = None


class YieldSnapshotOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    lot_id: UUID
    valuation_date: date
    market_snapshot_id: UUID | None
    purchase_total: Decimal
    planned_exit_total: Decimal
    planned_profit_before_tax: Decimal
    planned_profit_after_tax: Decimal
    planned_annual_yield_before_tax: Decimal
    planned_annual_yield_after_tax: Decimal
    current_exit_total: Decimal | None
    current_profit_before_tax: Decimal | None
    current_profit_after_tax: Decimal | None
    current_annual_yield_before_tax: Decimal | None
    current_annual_yield_after_tax: Decimal | None
    yield_delta_pp: Decimal | None
    calculation_version: str
    calculation_details: dict[str, Any]
    created_at: datetime


class CalculationOut(BaseModel):
    snapshot: YieldSnapshotOut
    current_available: bool
    tax_result_is_estimate: bool = True


class SettingsOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    market_data_ttl_seconds: int
    tax_mode: TaxMode
    tax_rate: Decimal
    legacy_tax_compatibility: bool
    default_sale_commission_rub_per_bond: Decimal
    timezone: str
    formula_version: str


class SettingsPatch(BaseModel):
    market_data_ttl_seconds: int | None = Field(default=None, ge=60)
    tax_mode: TaxMode | None = None
    tax_rate: Decimal | None = Field(default=None, ge=0, le=1)
    legacy_tax_compatibility: bool | None = None
    default_sale_commission_rub_per_bond: Decimal | None = Field(default=None, ge=0)
    timezone: str | None = None


class ImportRowOut(BaseModel):
    row_number: int
    source_isin: str
    normalized_isin: str
    source_name: str | None
    target_event_type: str
    purchase_date: date
    target_event_date: date
    quantity: Decimal
    purchase_clean_price_rub_per_bond: Decimal
    purchase_accrued_interest_rub_per_bond: Decimal
    purchase_commission_rub_per_bond: Decimal


class ImportErrorOut(BaseModel):
    row_number: int
    field: str
    message: str
    source_value: str | None


class ImportPreviewOut(BaseModel):
    preview_id: UUID
    file_name: str
    sheet_name: str
    checksum: str
    header_row_number: int
    rows_read: int
    rows: list[ImportRowOut]
    errors: list[ImportErrorOut]


class ImportCommitRequest(BaseModel):
    preview_id: UUID


class ImportBatchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    file_name: str
    sheet_name: str
    imported_at: datetime
    rows_read: int
    lots_created: int
    instruments_updated: int
    row_errors: list[dict[str, Any]]
    checksum: str
    idempotent_replay: bool = False


class RefreshAllOut(BaseModel):
    refreshed: list[str]
    errors: dict[str, str]
