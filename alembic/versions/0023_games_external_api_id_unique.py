"""Partial unique index on games.external_api_id

Revision ID: 0023
Revises: 0022
Create Date: 2026-10-09

One non-null external_api_id per row. Nulls stay allowed. Existing bare
values are left as stored. A duplicate non-null string aborts this upgrade.
No check constraint: the bare rows already in the table would fail one.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "uq_games_external_api_id",
        "games",
        ["external_api_id"],
        unique=True,
        postgresql_where=sa.text("external_api_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_games_external_api_id", table_name="games")
