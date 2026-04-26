"""Partial index on game_sessions(deleted_at) WHERE deleted_at IS NOT NULL

Revision ID: 0005
Revises: 0004
Create Date: 2026-04-26

Speeds up the daily hard-delete sweeper, which scans only soft-deleted
rows. The partial predicate keeps the index minimal — it only contains
rows pending hard delete.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.create_index(
            "ix_game_sessions_deleted_at",
            "game_sessions",
            ["deleted_at"],
            postgresql_concurrently=True,
            postgresql_where=sa.text("deleted_at IS NOT NULL"),
            if_not_exists=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            "ix_game_sessions_deleted_at",
            table_name="game_sessions",
            postgresql_concurrently=True,
            if_exists=True,
        )
