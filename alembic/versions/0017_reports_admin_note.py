"""Add reports.admin_note free-text field

Revision ID: 0017
Revises: 0016
Create Date: 2026-07-28

Lets an admin leave a triage note on a report. Nullable, no default,
no backfill — existing rows simply have no note.
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "reports",
        sa.Column("admin_note", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("reports", "admin_note")
