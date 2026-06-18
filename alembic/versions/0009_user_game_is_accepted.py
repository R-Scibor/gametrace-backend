"""Add is_accepted to user_game_preferences

Revision ID: 0009
Revises: 0008
Create Date: 2026-06-18

Tracks whether the user accepted a NEEDS_REVIEW stub into their library.
NULL when not applicable (ENRICHED/PENDING). Backfills inbox rows for
existing NEEDS_REVIEW games.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "user_game_preferences",
        sa.Column("is_accepted", sa.Boolean(), nullable=True),
    )
    op.execute(
        """
        INSERT INTO user_game_preferences (user_id, game_id, is_ignored, is_accepted)
        SELECT DISTINCT gs.user_id, gs.game_id, false, false
        FROM game_sessions gs
        JOIN games g ON g.id = gs.game_id
        WHERE g.enrichment_status = 'NEEDS_REVIEW'
          AND gs.deleted_at IS NULL
          AND gs.is_flicker = false
        ON CONFLICT (user_id, game_id) DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_column("user_game_preferences", "is_accepted")