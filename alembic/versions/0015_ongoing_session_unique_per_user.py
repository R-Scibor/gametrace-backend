"""Partial unique index: one ONGOING session per user

Revision ID: 0015
Revises: 0014
Create Date: 2026-07-11

Deduplicates any existing ONGOING rows (keeps the newest per user), then adds a
partial unique index so the invariant survives concurrent bot handlers.
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEDUP_NOTES = (
    "Duplicate ONGOING reconciled before unique-index migration (0015)."
)


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE game_sessions AS gs
            SET status = 'ERROR',
                notes = :notes
            FROM (
                SELECT id
                FROM (
                    SELECT id,
                           ROW_NUMBER() OVER (
                               PARTITION BY user_id
                               ORDER BY start_time DESC, id DESC
                           ) AS rn
                    FROM game_sessions
                    WHERE status = 'ONGOING'
                      AND deleted_at IS NULL
                ) ranked
                WHERE rn > 1
            ) dupes
            WHERE gs.id = dupes.id
            """
        ).bindparams(notes=_DEDUP_NOTES)
    )

    with op.get_context().autocommit_block():
        op.create_index(
            "uq_game_sessions_user_ongoing",
            "game_sessions",
            ["user_id"],
            unique=True,
            postgresql_concurrently=True,
            postgresql_where=sa.text("status = 'ONGOING' AND deleted_at IS NULL"),
            if_not_exists=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            "uq_game_sessions_user_ongoing",
            table_name="game_sessions",
            postgresql_concurrently=True,
            if_exists=True,
        )