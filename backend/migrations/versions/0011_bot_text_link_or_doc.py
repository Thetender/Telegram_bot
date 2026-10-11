"""bot_texts: link or document instead of text

Revision ID: 0011
Revises: 0010
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUMNS = ("mode", "button_text", "button_url", "file_name", "file_content", "file_id")


def upgrade() -> None:
    op.add_column(
        "bot_texts", sa.Column("mode", sa.String(length=8), server_default="text", nullable=False)
    )
    op.add_column("bot_texts", sa.Column("button_text", sa.String(length=64), nullable=True))
    op.add_column("bot_texts", sa.Column("button_url", sa.String(length=1024), nullable=True))
    op.add_column("bot_texts", sa.Column("file_name", sa.String(length=255), nullable=True))
    op.add_column("bot_texts", sa.Column("file_content", sa.LargeBinary(), nullable=True))
    op.add_column("bot_texts", sa.Column("file_id", sa.String(length=255), nullable=True))


def downgrade() -> None:
    for name in reversed(COLUMNS):
        op.drop_column("bot_texts", name)
