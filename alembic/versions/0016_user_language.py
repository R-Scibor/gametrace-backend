"""Add users.language profile field

Revision ID: 0016
Revises: 0015
Create Date: 2026-07-17

Adds the language preference used by the mobile client for i18n (PL/EN).
Existing rows backfill to 'pl' via the server_default, so current testers
stay Polish until they toggle.
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "language",
            sa.String(length=8),
            nullable=False,
            server_default="pl",
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "language")
