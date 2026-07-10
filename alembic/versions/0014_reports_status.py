"""Add reports.status column + triage index

Revision ID: 0014
Revises: 0013
Create Date: 2026-07-10

Adds a triage status column to the reports table for the admin reports
inbox: open | triaged | closed.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "reports",
        sa.Column(
            "status", sa.String(length=16), server_default="open", nullable=False
        ),
    )
    op.create_check_constraint(
        "ck_reports_status", "reports", "status IN ('open','triaged','closed')"
    )
    op.create_index(
        "ix_reports_status_created_at",
        "reports",
        ["status", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_reports_status_created_at", table_name="reports")
    op.drop_constraint("ck_reports_status", "reports", type_="check")
    op.drop_column("reports", "status")
