from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bond_trading.domain.calculations.models import ActionType, TaxMode
from bond_trading.infrastructure.db.base import (
    Base,
    TimestampMixin,
    UuidPrimaryKeyMixin,
    utc_now,
)

JSON_TYPE = JSON().with_variant(JSONB, "postgresql")
MONEY = Numeric(24, 8)
PERCENT = Numeric(24, 10)


class UserRole(StrEnum):
    USER = "user"
    ADMIN = "admin"


class UserModel(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(512))
    role: Mapped[UserRole] = mapped_column(
        Enum(
            UserRole,
            native_enum=False,
            values_callable=lambda enum: [item.value for item in enum],
        ),
        default=UserRole.USER,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuthSessionModel(UuidPrimaryKeyMixin, Base):
    __tablename__ = "auth_sessions"

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    csrf_token_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    user_agent: Mapped[str | None] = mapped_column(String(512))

    user: Mapped[UserModel] = relationship()


class BondInstrumentModel(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "bond_instruments"

    isin: Mapped[str] = mapped_column(String(12), unique=True, index=True)
    secid: Mapped[str] = mapped_column(String(64), index=True)
    short_name: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str | None] = mapped_column(String(512))
    primary_board_id: Mapped[str | None] = mapped_column(String(32))
    currency: Mapped[str] = mapped_column(String(8), default="RUB")
    initial_face_value: Mapped[Decimal | None] = mapped_column(MONEY)
    current_face_value: Mapped[Decimal | None] = mapped_column(MONEY)
    maturity_date: Mapped[date | None] = mapped_column(Date)
    offer_date: Mapped[date | None] = mapped_column(Date)
    coupon_period_days: Mapped[int | None] = mapped_column(Integer)
    is_amortizing: Mapped[bool] = mapped_column(Boolean, default=False)
    is_floating_coupon: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    lots: Mapped[list["BondLotModel"]] = relationship(back_populates="instrument")


class ImportBatchModel(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "import_batches"
    __table_args__ = (UniqueConstraint("owner_id", "checksum", "sheet_name"),)

    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    uploaded_file_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("uploaded_files.id", ondelete="SET NULL"), unique=True
    )
    file_name: Mapped[str] = mapped_column(String(512))
    sheet_name: Mapped[str] = mapped_column(String(255))
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    rows_read: Mapped[int] = mapped_column(Integer, default=0)
    lots_created: Mapped[int] = mapped_column(Integer, default=0)
    instruments_updated: Mapped[int] = mapped_column(Integer, default=0)
    row_errors: Mapped[list[dict[str, Any]]] = mapped_column(JSON_TYPE, default=list)
    checksum: Mapped[str] = mapped_column(String(64))


class UploadedFileModel(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "uploaded_files"

    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    original_file_name: Mapped[str] = mapped_column(String(512))
    object_key: Mapped[str] = mapped_column(String(1024), unique=True)
    content_type: Mapped[str] = mapped_column(String(255))
    file_format: Mapped[str] = mapped_column(String(16))
    size_bytes: Mapped[int] = mapped_column(Integer)
    checksum: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), default="uploaded", index=True)
    parse_error: Mapped[str | None] = mapped_column(Text)


class BondLotModel(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "bond_lots"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="positive_quantity"),
        UniqueConstraint("import_batch_id", "source_row_number"),
    )

    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    instrument_id: Mapped[UUID] = mapped_column(
        ForeignKey("bond_instruments.id", ondelete="RESTRICT"), index=True
    )
    purchase_date: Mapped[date] = mapped_column(Date)
    quantity: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    purchase_clean_price_rub_per_bond: Mapped[Decimal] = mapped_column(MONEY)
    purchase_accrued_interest_rub_per_bond: Mapped[Decimal] = mapped_column(MONEY)
    purchase_commission_rub_per_bond: Mapped[Decimal] = mapped_column(MONEY)
    target_event_type: Mapped[str] = mapped_column(String(16))
    target_event_date: Mapped[date] = mapped_column(Date)
    target_redemption_price_rub_per_bond: Mapped[Decimal | None] = mapped_column(MONEY)
    target_redemption_override_reason: Mapped[str | None] = mapped_column(Text)
    target_redemption_override_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    sale_commission_rub_per_bond: Mapped[Decimal] = mapped_column(MONEY, default=Decimal(0))
    planned_yield_manual_reference: Mapped[Decimal | None] = mapped_column(PERCENT)
    source_row_number: Mapped[int | None] = mapped_column(Integer)
    source_sheet_name: Mapped[str] = mapped_column(String(255), default="manual")
    notes: Mapped[str | None] = mapped_column(Text)
    import_batch_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("import_batches.id", ondelete="SET NULL"), index=True
    )

    instrument: Mapped[BondInstrumentModel] = relationship(back_populates="lots")


class CorporateActionModel(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "corporate_actions"
    __table_args__ = (
        UniqueConstraint("instrument_id", "action_type", "event_date", "source_payload_hash"),
    )

    instrument_id: Mapped[UUID] = mapped_column(
        ForeignKey("bond_instruments.id", ondelete="CASCADE"), index=True
    )
    action_type: Mapped[ActionType] = mapped_column(
        Enum(
            ActionType,
            native_enum=False,
            values_callable=lambda enum: [item.value for item in enum],
        )
    )
    event_date: Mapped[date] = mapped_column(Date, index=True)
    record_date: Mapped[date | None] = mapped_column(Date)
    amount_rub_per_bond: Mapped[Decimal | None] = mapped_column(MONEY)
    percent: Mapped[Decimal | None] = mapped_column(PERCENT)
    source: Mapped[str] = mapped_column(String(32), default="MOEX ISS")
    source_payload_hash: Mapped[str] = mapped_column(String(64))
    source_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MarketSnapshotModel(UuidPrimaryKeyMixin, Base):
    __tablename__ = "market_snapshots"

    instrument_id: Mapped[UUID] = mapped_column(
        ForeignKey("bond_instruments.id", ondelete="CASCADE"), index=True
    )
    board_id: Mapped[str | None] = mapped_column(String(32))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    market_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    bid_percent: Mapped[Decimal | None] = mapped_column(PERCENT)
    bid_rub_per_bond: Mapped[Decimal | None] = mapped_column(MONEY)
    bid_depth_lots: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    lot_size: Mapped[Decimal] = mapped_column(Numeric(24, 8), default=Decimal(1))
    current_face_value: Mapped[Decimal | None] = mapped_column(MONEY)
    accrued_interest_rub_per_bond: Mapped[Decimal | None] = mapped_column(MONEY)
    last_price_percent: Mapped[Decimal | None] = mapped_column(PERCENT)
    status: Mapped[str] = mapped_column(String(32))
    delayed_status: Mapped[str] = mapped_column(String(16), default="unknown")
    error_message: Mapped[str | None] = mapped_column(Text)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict)


class YieldSnapshotModel(UuidPrimaryKeyMixin, Base):
    __tablename__ = "yield_snapshots"

    lot_id: Mapped[UUID] = mapped_column(ForeignKey("bond_lots.id", ondelete="CASCADE"), index=True)
    valuation_date: Mapped[date] = mapped_column(Date, index=True)
    market_snapshot_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("market_snapshots.id", ondelete="SET NULL")
    )
    purchase_total: Mapped[Decimal] = mapped_column(MONEY)
    planned_exit_total: Mapped[Decimal] = mapped_column(MONEY)
    planned_profit_before_tax: Mapped[Decimal] = mapped_column(MONEY)
    planned_profit_after_tax: Mapped[Decimal] = mapped_column(MONEY)
    planned_annual_yield_before_tax: Mapped[Decimal] = mapped_column(PERCENT)
    planned_annual_yield_after_tax: Mapped[Decimal] = mapped_column(PERCENT)
    current_exit_total: Mapped[Decimal | None] = mapped_column(MONEY)
    current_profit_before_tax: Mapped[Decimal | None] = mapped_column(MONEY)
    current_profit_after_tax: Mapped[Decimal | None] = mapped_column(MONEY)
    current_annual_yield_before_tax: Mapped[Decimal | None] = mapped_column(PERCENT)
    current_annual_yield_after_tax: Mapped[Decimal | None] = mapped_column(PERCENT)
    yield_delta_pp: Mapped[Decimal | None] = mapped_column(PERCENT)
    calculation_version: Mapped[str] = mapped_column(String(32))
    calculation_details: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AppSettingModel(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "app_settings"
    __table_args__ = (UniqueConstraint("owner_id", "singleton_key"),)

    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    singleton_key: Mapped[str] = mapped_column(String(32), default="default")
    market_data_ttl_seconds: Mapped[int] = mapped_column(Integer, default=900)
    tax_mode: Mapped[TaxMode] = mapped_column(
        Enum(
            TaxMode, native_enum=False, values_callable=lambda enum: [item.value for item in enum]
        ),
        default=TaxMode.NONE,
    )
    tax_rate: Mapped[Decimal] = mapped_column(PERCENT, default=Decimal("0.13"))
    legacy_tax_compatibility: Mapped[bool] = mapped_column(Boolean, default=False)
    default_sale_commission_rub_per_bond: Mapped[Decimal] = mapped_column(MONEY, default=Decimal(0))
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Moscow")
    formula_version: Mapped[str] = mapped_column(String(32), default="1.0.0")
