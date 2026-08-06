"""Добавить подключения Google Таблиц и очередь синхронизации."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260801_0003"
down_revision: str | None = "20260725_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = sa.Uuid()


def timestamps() -> list[sa.Column[object]]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "sheet_connections",
        sa.Column("id", UUID, nullable=False),
        sa.Column("owner_id", UUID, nullable=False),
        sa.Column("provider", sa.String(32), server_default="google_sheets", nullable=False),
        sa.Column("spreadsheet_id", sa.String(255), nullable=False),
        sa.Column("worksheet_name", sa.String(255), nullable=False),
        sa.Column("header_row", sa.Integer(), server_default="1", nullable=False),
        sa.Column("isin_column", sa.String(8), server_default="A", nullable=False),
        sa.Column("price_column", sa.String(8), server_default="C", nullable=False),
        sa.Column("updated_at_column", sa.String(8)),
        sa.Column("status_column", sa.String(8)),
        sa.Column("price_mode", sa.String(32), server_default="best_bid_clean_rub", nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("sync_interval_seconds", sa.Integer(), server_default="300", nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("last_success_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
        sa.Column("last_payload_hash", sa.String(64)),
        *timestamps(),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_sheet_connections"),
        sa.UniqueConstraint("owner_id", "provider", name="uq_sheet_connections_owner_id_provider"),
    )
    op.create_index("ix_sheet_connections_owner_id", "sheet_connections", ["owner_id"])

    op.create_table(
        "sheet_sync_jobs",
        sa.Column("id", UUID, nullable=False),
        sa.Column("connection_id", UUID, nullable=False),
        sa.Column("trigger", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), server_default="queued", nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("rows_read", sa.Integer(), server_default="0", nullable=False),
        sa.Column("rows_updated", sa.Integer(), server_default="0", nullable=False),
        sa.Column("instruments_refreshed", sa.Integer(), server_default="0", nullable=False),
        sa.Column("row_errors", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("error_message", sa.Text()),
        *timestamps(),
        sa.ForeignKeyConstraint(["connection_id"], ["sheet_connections.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_sheet_sync_jobs"),
    )
    op.create_index("ix_sheet_sync_jobs_connection_id", "sheet_sync_jobs", ["connection_id"])
    op.create_index("ix_sheet_sync_jobs_status", "sheet_sync_jobs", ["status"])
    op.create_index("ix_sheet_sync_jobs_next_attempt_at", "sheet_sync_jobs", ["next_attempt_at"])


def downgrade() -> None:
    op.drop_index("ix_sheet_sync_jobs_next_attempt_at", table_name="sheet_sync_jobs")
    op.drop_index("ix_sheet_sync_jobs_status", table_name="sheet_sync_jobs")
    op.drop_index("ix_sheet_sync_jobs_connection_id", table_name="sheet_sync_jobs")
    op.drop_table("sheet_sync_jobs")
    op.drop_index("ix_sheet_connections_owner_id", table_name="sheet_connections")
    op.drop_table("sheet_connections")
