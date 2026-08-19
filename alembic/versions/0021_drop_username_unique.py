"""Drop the unique constraint on users.username

Revision ID: 0021
Revises: 0020
Create Date: 2026-08-19

Identity is discord_id; username is display-only. Discord usernames are
renameable by their owner, so one user's rename could collide with another
account and fail that account's OAuth login (409). The index itself is kept —
the dev-only name-only login still looks users up by it — just not unique.

Reverses 0002, which added the unique index back when name-only login was the
only login path and needed the name to resolve to exactly one account.
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_users_username", table_name="users")
    op.create_index("ix_users_username", "users", ["username"], unique=False)


def downgrade() -> None:
    # Fails if duplicate usernames have accumulated since the upgrade; that is
    # correct — the data no longer satisfies the constraint being restored.
    op.drop_index("ix_users_username", table_name="users")
    op.create_index("ix_users_username", "users", ["username"], unique=True)
