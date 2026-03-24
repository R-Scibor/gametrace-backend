"""Initial schema

Revision ID: 0001
Revises:
Create Date: 2026-03-24

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("discord_id", sa.String(32), primary_key=True),
        sa.Column("username", sa.String(100), nullable=False),
        sa.Column("timezone", sa.String(64), server_default="UTC", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    op.create_table(
        "user_auth_tokens",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "user_id",
            sa.String(32),
            sa.ForeignKey("users.discord_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token", sa.String(64), unique=True, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_active",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_user_auth_tokens_token", "user_auth_tokens", ["token"])

    op.create_table(
        "user_devices",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "user_id",
            sa.String(32),
            sa.ForeignKey("users.discord_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("fcm_token", sa.String(512), unique=True, nullable=False),
        sa.Column("device_type", sa.String(32), nullable=False),
        sa.Column(
            "last_active",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    op.create_table(
        "games",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("primary_name", sa.String(256), nullable=False),
        sa.Column("external_api_id", sa.String(64), nullable=True),
        sa.Column("cover_image_url", sa.String(512), nullable=True),
        sa.Column("cover_source", sa.String(16), server_default="EXTERNAL", nullable=False),
        sa.Column(
            "enrichment_status", sa.String(16), server_default="PENDING", nullable=False
        ),
    )

    op.create_table(
        "game_aliases",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "game_id",
            sa.Integer,
            sa.ForeignKey("games.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("discord_process_name", sa.String(256), unique=True, nullable=False),
    )
    op.create_index(
        "ix_game_aliases_discord_process_name", "game_aliases", ["discord_process_name"]
    )

    op.create_table(
        "user_game_preferences",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "user_id",
            sa.String(32),
            sa.ForeignKey("users.discord_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "game_id", sa.Integer, sa.ForeignKey("games.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("is_ignored", sa.Boolean, server_default="false", nullable=False),
        sa.Column("custom_tag", sa.String(64), nullable=True),
        sa.UniqueConstraint("user_id", "game_id", name="uq_user_game_preferences"),
    )

    op.create_table(
        "game_sessions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "user_id",
            sa.String(32),
            sa.ForeignKey("users.discord_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("game_id", sa.Integer, sa.ForeignKey("games.id"), nullable=False),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_seconds", sa.Integer, nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

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


def downgrade() -> None:
    op.drop_table("daily_user_stats")
    op.drop_table("game_sessions")
    op.drop_table("user_game_preferences")
    op.drop_index("ix_game_aliases_discord_process_name", "game_aliases")
    op.drop_table("game_aliases")
    op.drop_table("games")
    op.drop_table("user_devices")
    op.drop_index("ix_user_auth_tokens_token", "user_auth_tokens")
    op.drop_table("user_auth_tokens")
    op.drop_table("users")
