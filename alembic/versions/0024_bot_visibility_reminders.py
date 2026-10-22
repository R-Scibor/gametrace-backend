"""Bot visibility reminder settings on users

Revision ID: 0024
Revises: 0023
Create Date: 2026-10-22

Two user settings for the web reminder shown when the Discord bot cannot see
the user: an opt-out flag (default on) and an optional snooze deadline.
Visibility itself is not stored here; it lives in a Redis snapshot.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "bot_visibility_reminders_enabled",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "bot_visibility_reminder_snoozed_until",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "bot_visibility_reminder_snoozed_until")
    op.drop_column("users", "bot_visibility_reminders_enabled")
