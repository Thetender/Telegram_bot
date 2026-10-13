"""monitorings.created_by

Revision ID: 0013
Revises: 0012
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("monitorings", sa.Column("created_by", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_monitorings_created_by_users",
        "monitorings",
        "users",
        ["created_by"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_monitorings_created_by_users", "monitorings", type_="foreignkey")
    op.drop_column("monitorings", "created_by")
