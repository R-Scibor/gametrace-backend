"""Add user account deletion schedule columns

Revision ID: 0018
Revises: 0017
Create Date: 2026-08-03

Adds `deletion_requested_at` and `purge_at` to `users`, tracking a
self-service account deletion request and the timestamp at which the
grace period (`settings.account_deletion_grace_days`) expires and the
account is eligible for permanent purge. Both nullable, no server
default — purely additive, safe against the live DB.
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("deletion_requested_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("purge_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_users_purge_at_partial",
        "users",
        ["purge_at"],
        unique=False,
        postgresql_where=sa.text("purge_at IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_users_purge_at_partial", table_name="users")
    op.drop_column("users", "purge_at")
    op.drop_column("users", "deletion_requested_at")
