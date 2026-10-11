"""users.acquisition_source

Revision ID: 0010
Revises: 0009
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("acquisition_source", sa.String(length=64), nullable=True))
    op.create_index(
        op.f("ix_users_acquisition_source"), "users", ["acquisition_source"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_users_acquisition_source"), table_name="users")
    op.drop_column("users", "acquisition_source")
