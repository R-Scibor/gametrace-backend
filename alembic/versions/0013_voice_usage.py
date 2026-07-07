"""Add voice_usage table

Revision ID: 0013
Revises: 0012
Create Date: 2026-07-07

Per-call metadata for the paid voice pipeline: cost/usage analytics only, no
transcript text.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "voice_usage",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("audio_seconds", sa.Numeric(), nullable=True),
        sa.Column("detected_language", sa.String(length=32), nullable=True),
        sa.Column("game_resolved", sa.Boolean(), nullable=False),
        sa.Column("fields_extracted", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.discord_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_voice_usage_user_id", "voice_usage", ["user_id"])
    op.create_index("ix_voice_usage_created_at", "voice_usage", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_voice_usage_created_at", table_name="voice_usage")
    op.drop_index("ix_voice_usage_user_id", table_name="voice_usage")
    op.drop_table("voice_usage")