"""Drop daily_user_stats table

Revision ID: 0006
Revises: 0005
Create Date: 2026-04-26

The Downsampling Engine was removed from Phase 5 (see
docs/internal/scaling_for_released_app.md). The table was never written
to in production, and the merge endpoint has been simplified to skip it.
Drop the dead schema.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table("daily_user_stats")


def downgrade() -> None:
    op.create_table(
        "daily_user_stats",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "user_id",
            sa.String(32),
            sa.ForeignKey("users.discord_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("game_id", sa.Integer, sa.ForeignKey("games.id"), nullable=False),
        sa.Column("date", sa.Date, nullable=False),
        sa.Column("total_seconds", sa.Integer, default=0, nullable=False),
        sa.UniqueConstraint("user_id", "game_id", "date", name="uq_daily_user_stats"),
    )
