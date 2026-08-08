"""Add account_deletion_events Art. 17 audit table

Revision ID: 0019
Revises: 0018
Create Date: 2026-08-08

Append-only erasure trail. No FK to users — rows outlive hard purge.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.models.account_deletion_event import _VALID_EVENTS

revision: str = "0019"
down_revision: Union[str, None] = "0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "account_deletion_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("discord_id", sa.String(length=32), nullable=False),
        sa.Column("event", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("purge_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "event IN (" + ", ".join(f"'{e}'" for e in _VALID_EVENTS) + ")",
            name="ck_account_deletion_events_event",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_account_deletion_events_discord_id_created_at",
        "account_deletion_events",
        ["discord_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_account_deletion_events_discord_id_created_at",
        table_name="account_deletion_events",
    )
    op.drop_table("account_deletion_events")
