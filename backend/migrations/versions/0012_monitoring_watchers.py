"""monitoring watchers + watcher copies of notifications

Revision ID: 0012
Revises: 0011
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "monitoring_watchers",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("monitoring_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("added_by", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["monitoring_id"], ["monitorings.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["added_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("monitoring_id", "user_id", name="uq_monitoring_watcher"),
    )
    op.create_index("ix_monitoring_watchers_monitoring_id", "monitoring_watchers", ["monitoring_id"])
    op.create_index("ix_monitoring_watchers_user_id", "monitoring_watchers", ["user_id"])
    op.add_column(
        "notification_deliveries",
        sa.Column("for_user_id", sa.BigInteger(), server_default="0", nullable=False),
    )
    op.drop_constraint("uq_delivery_auction_user", "notification_deliveries", type_="unique")
    op.create_unique_constraint(
        "uq_delivery_auction_user_for",
        "notification_deliveries",
        ["canonical_auction_id", "telegram_user_id", "for_user_id"],
    )


def downgrade() -> None:
    op.execute("DELETE FROM notification_deliveries WHERE for_user_id <> 0")
    op.drop_constraint("uq_delivery_auction_user_for", "notification_deliveries", type_="unique")
    op.create_unique_constraint(
        "uq_delivery_auction_user",
        "notification_deliveries",
        ["canonical_auction_id", "telegram_user_id"],
    )
    op.drop_column("notification_deliveries", "for_user_id")
    op.drop_index("ix_monitoring_watchers_user_id", table_name="monitoring_watchers")
    op.drop_index("ix_monitoring_watchers_monitoring_id", table_name="monitoring_watchers")
    op.drop_table("monitoring_watchers")
