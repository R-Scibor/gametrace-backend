"""Add users.is_admin flag

Revision ID: 0011
Revises: 0010
Create Date: 2026-07-03

Adds the is_admin flag used to gate RBAC-protected admin endpoints.
No admin rows are seeded here; operators promote users manually.
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "is_admin",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "is_admin")
