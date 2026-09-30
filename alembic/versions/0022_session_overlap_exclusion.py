"""Exclude overlapping live session ranges

Revision ID: 0022
Revises: 0021
Create Date: 2026-09-30

Live, non-flicker ONGOING and COMPLETED rows for one user cannot overlap.
The range is half-open, so a session that ends at the same instant another
starts is allowed. ERROR, flicker, and soft-deleted rows stay outside it.

Existing overlaps are reconciled before the constraint is added, or the
ALTER fails. Rows are walked per user in start order. A later row that
sticks out past an earlier one keeps the tail and moves its start to the
earlier end. A later row with nothing left outside the earlier range becomes
ERROR. Downgrade drops the constraint and does not restore those rows.
"""
from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa

from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NOTE = "Overlapping session reconciled before exclusion constraint (0022)."


def _overlaps(
    start: datetime,
    end: datetime | None,
    kept_start: datetime,
    kept_end: datetime | None,
) -> bool:
    if kept_end is not None and start >= kept_end:
        return False
    if end is not None and end <= kept_start:
        return False
    return True


def _fully_covered(
    start: datetime,
    end: datetime | None,
    kept_start: datetime,
    kept_end: datetime | None,
) -> bool:
    if start < kept_start:
        return False
    if kept_end is None:
        return True
    if end is None:
        return False
    return end <= kept_end


def reconcile_overlapping_sessions(connection) -> tuple[int, int]:
    """Trim or error live rows so the exclusion constraint can be validated.

    Returns (trimmed, errored). None end_time means the range runs to infinity.
    """
    rows = connection.execute(
        sa.text(
            """
            SELECT id, user_id, start_time, end_time, status, notes
            FROM game_sessions
            WHERE deleted_at IS NULL
              AND is_flicker = false
              AND status IN ('ONGOING', 'COMPLETED')
            ORDER BY user_id, start_time, id
            FOR UPDATE
            """
        )
    ).mappings().all()

    kept: dict[str, list[tuple[datetime, datetime | None]]] = {}
    trimmed = 0
    errored = 0
    for row in rows:
        ranges = kept.setdefault(row["user_id"], [])
        start = row["start_time"]
        end = row["end_time"]
        covered = False
        for kept_start, kept_end in ranges:
            if not _overlaps(start, end, kept_start, kept_end):
                continue
            if _fully_covered(start, end, kept_start, kept_end) or kept_end is None:
                covered = True
                break
            start = kept_end
        if end is not None and start >= end:
            covered = True
        if covered:
            connection.execute(
                sa.text(
                    """
                    UPDATE game_sessions
                    SET status = 'ERROR',
                        notes = CASE
                            WHEN notes IS NULL OR notes = '' THEN :note
                            ELSE notes || E'\\n' || :note
                        END
                    WHERE id = :id
                    """
                ),
                {"id": row["id"], "note": _NOTE},
            )
            errored += 1
            continue
        if start != row["start_time"]:
            duration = None if end is None else int((end - start).total_seconds())
            connection.execute(
                sa.text(
                    """
                    UPDATE game_sessions
                    SET start_time = :start_time,
                        duration_seconds = :duration
                    WHERE id = :id
                    """
                ),
                {"id": row["id"], "start_time": start, "duration": duration},
            )
            trimmed += 1
        ranges.append((start, end))
    return trimmed, errored


def upgrade() -> None:
    op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS btree_gist"))
    reconcile_overlapping_sessions(op.get_bind())
    op.execute(
        sa.text(
            """
            ALTER TABLE game_sessions
            ADD CONSTRAINT excl_game_sessions_no_overlap
            EXCLUDE USING gist (
                user_id WITH =,
                tstzrange(start_time, COALESCE(end_time, 'infinity'), '[)') WITH &&
            )
            WHERE (
                deleted_at IS NULL
                AND is_flicker = false
                AND status IN ('ONGOING', 'COMPLETED')
            )
            """
        )
    )


def downgrade() -> None:
    op.execute(sa.text("ALTER TABLE game_sessions DROP CONSTRAINT IF EXISTS excl_game_sessions_no_overlap"))
