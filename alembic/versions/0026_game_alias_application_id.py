"""Nullable Discord application id on a process-name alias.

Revision ID: 0026
Revises: 0025
Create Date: 2026-11-14

Existing rows stay null. The column is not unique.
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "game_aliases",
        sa.Column("application_id", sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("game_aliases", "application_id")
