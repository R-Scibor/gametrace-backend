"""
app/bot/flicker_policy.py

Pure-decision module for session flicker handling.
No side effects — only reads config and runs DB queries.
"""
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.session import GameSession, SessionSource, SessionStatus


def is_short_flicker(duration_seconds: int) -> bool:
    """Return True if the session is short enough to be treated as a flicker."""
    return duration_seconds < settings.session_short_flicker_seconds


async def find_stitch_candidate(
    db: AsyncSession,
    user_id: str,
    game_id: int,
) -> GameSession | None:
    """
    Return the most-recent BOT COMPLETED session for the same game that a
    restart should reopen, or None if no eligible candidate exists within
    the stitch window.

    NOTE: Does NOT filter on is_flicker — stitch must be able to reopen
    flicker-flagged rows (that is the whole point of stitching).
    """
    window_start = datetime.now(UTC) - timedelta(
        seconds=settings.session_stitch_window_seconds
    )
    stmt = (
        select(GameSession)
        .where(
            GameSession.user_id == user_id,
            GameSession.game_id == game_id,
            GameSession.source == SessionSource.BOT,
            GameSession.status == SessionStatus.COMPLETED,
            GameSession.deleted_at.is_(None),
            GameSession.end_time > window_start,
        )
        .order_by(GameSession.end_time.desc(), GameSession.id.desc())
        .limit(1)
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()
