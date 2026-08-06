"""Добавить лучшую цену предложения MOEX в рыночные снимки."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260807_0004"
down_revision: str | None = "20260801_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MONEY = sa.Numeric(24, 8)
PERCENT = sa.Numeric(24, 10)


def upgrade() -> None:
    op.add_column("market_snapshots", sa.Column("offer_percent", PERCENT))
    op.add_column("market_snapshots", sa.Column("offer_rub_per_bond", MONEY))
    op.add_column("market_snapshots", sa.Column("offer_depth_lots", sa.Numeric(24, 8)))
    op.alter_column(
        "sheet_connections",
        "price_mode",
        server_default="best_offer_clean_rub",
    )
    op.execute(
        "UPDATE sheet_connections SET price_mode = 'best_offer_clean_rub' "
        "WHERE price_mode = 'best_bid_clean_rub'"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE sheet_connections SET price_mode = 'best_bid_clean_rub' "
        "WHERE price_mode = 'best_offer_clean_rub'"
    )
    op.alter_column(
        "sheet_connections",
        "price_mode",
        server_default="best_bid_clean_rub",
    )
    op.drop_column("market_snapshots", "offer_depth_lots")
    op.drop_column("market_snapshots", "offer_rub_per_bond")
    op.drop_column("market_snapshots", "offer_percent")
