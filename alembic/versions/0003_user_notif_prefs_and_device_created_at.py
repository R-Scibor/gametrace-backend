"""User notification prefs and user_devices.created_at

Revision ID: 0003
Revises: 0002
Create Date: 2026-04-19

Adds weekly_report_enabled + push_enabled on users (both default true) and
created_at on user_devices (default now). Also indexes user_devices.user_id
for the weekly-report fan-out query.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "weekly_report_enabled",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "push_enabled",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
    )
    op.add_column(
        "user_devices",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_user_devices_user_id", "user_devices", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_user_devices_user_id", table_name="user_devices")
    op.drop_column("user_devices", "created_at")
    op.drop_column("users", "push_enabled")
    op.drop_column("users", "weekly_report_enabled")
