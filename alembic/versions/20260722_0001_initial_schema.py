"""Create the initial bond portfolio schema."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260722_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = sa.Uuid()
MONEY = sa.Numeric(24, 8)
PERCENT = sa.Numeric(24, 10)


def timestamps() -> list[sa.Column[object]]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "bond_instruments",
        sa.Column("id", UUID, nullable=False),
        sa.Column("isin", sa.String(12), nullable=False),
        sa.Column("secid", sa.String(64), nullable=False),
        sa.Column("short_name", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(512)),
        sa.Column("primary_board_id", sa.String(32)),
        sa.Column("currency", sa.String(8), nullable=False),
        sa.Column("initial_face_value", MONEY),
        sa.Column("current_face_value", MONEY),
        sa.Column("maturity_date", sa.Date()),
        sa.Column("offer_date", sa.Date()),
        sa.Column("coupon_period_days", sa.Integer()),
        sa.Column("is_amortizing", sa.Boolean(), nullable=False),
        sa.Column("is_floating_coupon", sa.Boolean(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("source_updated_at", sa.DateTime(timezone=True)),
        *timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_bond_instruments"),
        sa.UniqueConstraint("isin", name="uq_bond_instruments_isin"),
    )
    op.create_index("ix_bond_instruments_isin", "bond_instruments", ["isin"])
    op.create_index("ix_bond_instruments_secid", "bond_instruments", ["secid"])

    op.create_table(
        "import_batches",
        sa.Column("id", UUID, nullable=False),
        sa.Column("file_name", sa.String(512), nullable=False),
        sa.Column("sheet_name", sa.String(255), nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rows_read", sa.Integer(), nullable=False),
        sa.Column("lots_created", sa.Integer(), nullable=False),
        sa.Column("instruments_updated", sa.Integer(), nullable=False),
        sa.Column("row_errors", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        *timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_import_batches"),
        sa.UniqueConstraint("checksum", "sheet_name", name="uq_import_batches_checksum"),
    )

    op.create_table(
        "bond_lots",
        sa.Column("id", UUID, nullable=False),
        sa.Column("instrument_id", UUID, nullable=False),
        sa.Column("purchase_date", sa.Date(), nullable=False),
        sa.Column("quantity", sa.Numeric(24, 8), nullable=False),
        sa.Column("purchase_clean_price_rub_per_bond", MONEY, nullable=False),
        sa.Column("purchase_accrued_interest_rub_per_bond", MONEY, nullable=False),
        sa.Column("purchase_commission_rub_per_bond", MONEY, nullable=False),
        sa.Column("target_event_type", sa.String(16), nullable=False),
        sa.Column("target_event_date", sa.Date(), nullable=False),
        sa.Column("target_redemption_price_rub_per_bond", MONEY),
        sa.Column("sale_commission_rub_per_bond", MONEY, nullable=False),
        sa.Column("planned_yield_manual_reference", PERCENT),
        sa.Column("source_row_number", sa.Integer()),
        sa.Column("source_sheet_name", sa.String(255), nullable=False),
        sa.Column("notes", sa.Text()),
        sa.Column("import_batch_id", UUID),
        *timestamps(),
        sa.CheckConstraint("quantity > 0", name="ck_bond_lots_positive_quantity"),
        sa.ForeignKeyConstraint(["instrument_id"], ["bond_instruments.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["import_batch_id"], ["import_batches.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_bond_lots"),
        sa.UniqueConstraint(
            "import_batch_id", "source_row_number", name="uq_bond_lots_import_batch_id"
        ),
    )
    op.create_index("ix_bond_lots_instrument_id", "bond_lots", ["instrument_id"])
    op.create_index("ix_bond_lots_import_batch_id", "bond_lots", ["import_batch_id"])

    op.create_table(
        "corporate_actions",
        sa.Column("id", UUID, nullable=False),
        sa.Column("instrument_id", UUID, nullable=False),
        sa.Column("action_type", sa.String(16), nullable=False),
        sa.Column("event_date", sa.Date(), nullable=False),
        sa.Column("record_date", sa.Date()),
        sa.Column("amount_rub_per_bond", MONEY),
        sa.Column("percent", PERCENT),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("source_payload_hash", sa.String(64), nullable=False),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=False),
        *timestamps(),
        sa.ForeignKeyConstraint(["instrument_id"], ["bond_instruments.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_corporate_actions"),
        sa.UniqueConstraint(
            "instrument_id",
            "action_type",
            "event_date",
            "source_payload_hash",
            name="uq_corporate_actions_instrument_id",
        ),
    )
    op.create_index("ix_corporate_actions_instrument_id", "corporate_actions", ["instrument_id"])
    op.create_index("ix_corporate_actions_event_date", "corporate_actions", ["event_date"])

    op.create_table(
        "market_snapshots",
        sa.Column("id", UUID, nullable=False),
        sa.Column("instrument_id", UUID, nullable=False),
        sa.Column("board_id", sa.String(32)),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("market_timestamp", sa.DateTime(timezone=True)),
        sa.Column("bid_percent", PERCENT),
        sa.Column("bid_rub_per_bond", MONEY),
        sa.Column("bid_depth_lots", sa.Numeric(24, 8)),
        sa.Column("lot_size", sa.Numeric(24, 8), nullable=False),
        sa.Column("current_face_value", MONEY),
        sa.Column("accrued_interest_rub_per_bond", MONEY),
        sa.Column("last_price_percent", PERCENT),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("delayed_status", sa.String(16), nullable=False),
        sa.Column("error_message", sa.Text()),
        sa.Column("raw_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(["instrument_id"], ["bond_instruments.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_market_snapshots"),
    )
    op.create_index("ix_market_snapshots_instrument_id", "market_snapshots", ["instrument_id"])

    op.create_table(
        "yield_snapshots",
        sa.Column("id", UUID, nullable=False),
        sa.Column("lot_id", UUID, nullable=False),
        sa.Column("valuation_date", sa.Date(), nullable=False),
        sa.Column("market_snapshot_id", UUID),
        sa.Column("purchase_total", MONEY, nullable=False),
        sa.Column("planned_exit_total", MONEY, nullable=False),
        sa.Column("planned_profit_before_tax", MONEY, nullable=False),
        sa.Column("planned_profit_after_tax", MONEY, nullable=False),
        sa.Column("planned_annual_yield_before_tax", PERCENT, nullable=False),
        sa.Column("planned_annual_yield_after_tax", PERCENT, nullable=False),
        sa.Column("current_exit_total", MONEY),
        sa.Column("current_profit_before_tax", MONEY),
        sa.Column("current_profit_after_tax", MONEY),
        sa.Column("current_annual_yield_before_tax", PERCENT),
        sa.Column("current_annual_yield_after_tax", PERCENT),
        sa.Column("yield_delta_pp", PERCENT),
        sa.Column("calculation_version", sa.String(32), nullable=False),
        sa.Column("calculation_details", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["lot_id"], ["bond_lots.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["market_snapshot_id"], ["market_snapshots.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_yield_snapshots"),
    )
    op.create_index("ix_yield_snapshots_lot_id", "yield_snapshots", ["lot_id"])
    op.create_index("ix_yield_snapshots_valuation_date", "yield_snapshots", ["valuation_date"])

    op.create_table(
        "app_settings",
        sa.Column("id", UUID, nullable=False),
        sa.Column("singleton_key", sa.String(32), nullable=False),
        sa.Column("market_data_ttl_seconds", sa.Integer(), nullable=False),
        sa.Column("tax_mode", sa.String(32), nullable=False),
        sa.Column("tax_rate", PERCENT, nullable=False),
        sa.Column("legacy_tax_compatibility", sa.Boolean(), nullable=False),
        sa.Column("default_sale_commission_rub_per_bond", MONEY, nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("formula_version", sa.String(32), nullable=False),
        *timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_app_settings"),
        sa.UniqueConstraint("singleton_key", name="uq_app_settings_singleton_key"),
    )


def downgrade() -> None:
    op.drop_table("app_settings")
    op.drop_index("ix_yield_snapshots_valuation_date", table_name="yield_snapshots")
    op.drop_index("ix_yield_snapshots_lot_id", table_name="yield_snapshots")
    op.drop_table("yield_snapshots")
    op.drop_index("ix_market_snapshots_instrument_id", table_name="market_snapshots")
    op.drop_table("market_snapshots")
    op.drop_index("ix_corporate_actions_event_date", table_name="corporate_actions")
    op.drop_index("ix_corporate_actions_instrument_id", table_name="corporate_actions")
    op.drop_table("corporate_actions")
    op.drop_index("ix_bond_lots_import_batch_id", table_name="bond_lots")
    op.drop_index("ix_bond_lots_instrument_id", table_name="bond_lots")
    op.drop_table("bond_lots")
    op.drop_table("import_batches")
    op.drop_index("ix_bond_instruments_secid", table_name="bond_instruments")
    op.drop_index("ix_bond_instruments_isin", table_name="bond_instruments")
    op.drop_table("bond_instruments")
