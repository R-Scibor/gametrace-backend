"""Add demo seed tables and demo user row

Revision ID: 0020
Revises: 0019
Create Date: 2026-08-12

Creates `demo_seed_sessions` / `demo_seed_preferences` — pristine-snapshot
tables for the permanent Google Play reviewer demo account — and inserts
the demo `users` row.

The demo identity literals below intentionally duplicate the constants in
`app.services.demo` (DEMO_DISCORD_ID, DEMO_USERNAME, DEMO_TIMEZONE,
DEMO_LANGUAGE). Migrations must not import app code: app code can change
after this migration is frozen in history, which would silently alter what
an already-applied migration does.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Duplicated from app.services.demo — see module docstring.
_DEMO_DISCORD_ID = "1"
_DEMO_USERNAME = "GameTrace Reviewer"
_DEMO_TIMEZONE = "Europe/Warsaw"
_DEMO_LANGUAGE = "en"


def upgrade() -> None:
    op.create_table(
        "demo_seed_sessions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("game_id", sa.Integer(), nullable=False),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.ForeignKeyConstraint(["game_id"], ["games.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "demo_seed_preferences",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("game_id", sa.Integer(), nullable=False),
        sa.Column(
            "is_ignored", sa.Boolean(), server_default="false", nullable=False
        ),
        sa.Column("is_accepted", sa.Boolean(), nullable=True),
        sa.Column("custom_tag", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(["game_id"], ["games.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    users = sa.table(
        "users",
        sa.column("discord_id", sa.String),
        sa.column("username", sa.String),
        sa.column("timezone", sa.String),
        sa.column("language", sa.String),
        sa.column("is_admin", sa.Boolean),
    )
    stmt = pg_insert(users).values(
        discord_id=_DEMO_DISCORD_ID,
        username=_DEMO_USERNAME,
        timezone=_DEMO_TIMEZONE,
        language=_DEMO_LANGUAGE,
        is_admin=False,
    )
    op.execute(stmt.on_conflict_do_nothing(index_elements=["discord_id"]))


def downgrade() -> None:
    op.execute(
        sa.text("DELETE FROM users WHERE discord_id = :discord_id").bindparams(
            discord_id=_DEMO_DISCORD_ID
        )
    )
    op.drop_table("demo_seed_preferences")
    op.drop_table("demo_seed_sessions")
