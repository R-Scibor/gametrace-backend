"""Composite index on game_sessions(user_id, start_time)

Revision ID: 0004
Revises: 0003
Create Date: 2026-04-26

Covers overlap validation (POST/PATCH /sessions) and stats summary
(equality on user_id + range on start_time). Created CONCURRENTLY to
avoid locking writes; requires running outside a transaction.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.create_index(
            "ix_game_sessions_user_id_start_time",
            "game_sessions",
            ["user_id", "start_time"],
            postgresql_concurrently=True,
            if_not_exists=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            "ix_game_sessions_user_id_start_time",
            table_name="game_sessions",
            postgresql_concurrently=True,
            if_exists=True,
        )
