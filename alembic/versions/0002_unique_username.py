"""Unique index on users.username

Revision ID: 0002
Revises: 0001
Create Date: 2026-03-25

Discord usernames are globally unique since 2023 (discriminators removed).
The app now looks up users by username on login, so a unique index is required.
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_users_username", "users", ["username"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_users_username", table_name="users")
