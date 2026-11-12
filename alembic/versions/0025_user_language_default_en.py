"""Default users.language to en

Revision ID: 0025
Revises: 0024
Create Date: 2026-11-12

New accounts are English. Existing rows are left as stored.
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "users",
        "language",
        existing_type=sa.String(length=8),
        server_default="en",
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "users",
        "language",
        existing_type=sa.String(length=8),
        server_default="pl",
        existing_nullable=False,
    )
