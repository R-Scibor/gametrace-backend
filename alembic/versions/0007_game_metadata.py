"""Game metadata columns + GIN indexes

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-03

Adds first_release_date and JSONB lists for genres/themes/developers/
publishers on the games table, with GIN indexes for containment queries
used by the upcoming stats expansion (filter sessions by game metadata).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "games",
        sa.Column("first_release_date", sa.Date(), nullable=True),
    )
    op.add_column(
        "games",
        sa.Column(
            "genres",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "games",
        sa.Column(
            "themes",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "games",
        sa.Column(
            "developers",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "games",
        sa.Column(
            "publishers",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )

    op.create_index(
        "ix_games_genres_gin", "games", ["genres"], postgresql_using="gin"
    )
    op.create_index(
        "ix_games_themes_gin", "games", ["themes"], postgresql_using="gin"
    )
    op.create_index(
        "ix_games_developers_gin", "games", ["developers"], postgresql_using="gin"
    )
    op.create_index(
        "ix_games_publishers_gin", "games", ["publishers"], postgresql_using="gin"
    )


def downgrade() -> None:
    op.drop_index("ix_games_publishers_gin", table_name="games")
    op.drop_index("ix_games_developers_gin", table_name="games")
    op.drop_index("ix_games_themes_gin", table_name="games")
    op.drop_index("ix_games_genres_gin", table_name="games")

    op.drop_column("games", "publishers")
    op.drop_column("games", "developers")
    op.drop_column("games", "themes")
    op.drop_column("games", "genres")
    op.drop_column("games", "first_release_date")
