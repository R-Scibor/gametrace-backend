"""Add is_flicker column to game_sessions

Revision ID: 0008
Revises: 0007
Create Date: 2026-06-16

Flags bot-written sessions that are presence-flicker junk. Defaults to
false so existing rows backfill without a NOT NULL violation. The bot
sets/clears the flag; the API filters at SELECT.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "game_sessions",
        sa.Column("is_flicker", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    op.drop_column("game_sessions", "is_flicker")
