"""Add users.language profile field

Revision ID: 0016
Revises: 0015
Create Date: 2026-07-17

Adds the language preference used by the mobile client for i18n (PL/EN).
Existing rows backfill to 'pl' via the server_default, so current testers
stay Polish until they toggle.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


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
