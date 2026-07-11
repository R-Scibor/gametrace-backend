"""
Database operations used by the Discord bot.
All functions accept an AsyncSession and perform a single logical operation.
"""
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.flicker_policy import find_stitch_candidate, is_short_flicker
from app.models.game import EnrichmentStatus, Game, GameAlias
from app.services.game_review import ensure_inbox_for_user
from app.models.session import GameSession, SessionSource, SessionStatus
from app.models.user import User

logger = logging.getLogger(__name__)


async def get_user_if_tracked(db: AsyncSession, discord_id: str) -> User | None:
    """Return User only if they have already logged into the app (exist in users table)."""
    return await db.get(User, discord_id)


async def get_or_create_game(db: AsyncSession, process_name: str) -> tuple[Game, bool]:
    """
    Look up a game by Discord process name via game_aliases.
    If not found, create a stub Game + GameAlias.

    Returns (game, created) where created=True means a new stub was inserted.
    """
    result = await db.execute(
        select(GameAlias).where(GameAlias.discord_process_name == process_name)
    )
    alias = result.scalar_one_or_none()

    if alias:
        game = await db.get(Game, alias.game_id)
        return game, False

    # Create stub game + alias
    game = Game(primary_name=process_name)
    db.add(game)
    await db.flush()  # get game.id without full commit

    alias = GameAlias(game_id=game.id, discord_process_name=process_name)
    db.add(alias)
    await db.commit()
    await db.refresh(game)

    logger.info("Created stub game %r (id=%d)", process_name, game.id)
    return game, True


async def get_ongoing_session(db: AsyncSession, user_id: str) -> GameSession | None:
    """Return the current ONGOING session for a user, or None."""
    result = await db.execute(
        select(GameSession)
        .where(
            GameSession.user_id == user_id,
            GameSession.status == SessionStatus.ONGOING,
            GameSession.deleted_at.is_(None),
        )
        .order_by(GameSession.start_time.desc(), GameSession.id.desc())
    )
    sessions = list(result.scalars().all())
    if len(sessions) > 1:
        logger.warning(
            "Duplicate ONGOING sessions for user=%s: %s",
            user_id,
            [session.id for session in sessions],
        )
    return sessions[0] if sessions else None


async def start_session(db: AsyncSession, user_id: str, game_id: int) -> GameSession:
    """Create a new ONGOING BOT session."""
    game = await db.get(Game, game_id)
    session = GameSession(
        user_id=user_id,
        game_id=game_id,
        start_time=datetime.now(timezone.utc),
        status=SessionStatus.ONGOING,
        source=SessionSource.BOT,
    )
    db.add(session)
    if game is not None and game.enrichment_status == EnrichmentStatus.NEEDS_REVIEW:
        await ensure_inbox_for_user(db, game_id, user_id)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        existing = await get_ongoing_session(db, user_id)
        if existing is None:
            raise
        logger.warning(
            "ONGOING insert raced for user=%s; returning session_id=%d",
            user_id,
            existing.id,
        )
        return existing
    await db.refresh(session)
    logger.info("Session STARTED user=%s game_id=%d session_id=%d", user_id, game_id, session.id)
    return session


async def complete_session(db: AsyncSession, session: GameSession) -> GameSession:
    """Transition ONGOING → COMPLETED, fill end_time and duration."""
    now = datetime.now(timezone.utc)
    session.status = SessionStatus.COMPLETED
    session.end_time = now
    session.duration_seconds = int((now - session.start_time).total_seconds())
    session.is_flicker = session.source == SessionSource.BOT and is_short_flicker(session.duration_seconds)
    await db.commit()
    await db.refresh(session)
    logger.info(
        "Session COMPLETED session_id=%d duration=%ds",
        session.id,
        session.duration_seconds,
    )
    return session


async def start_or_resume_session(db: AsyncSession, user_id: str, game_id: int) -> GameSession:
    """Reopen a recent same-game BOT session if within the stitch window, else start fresh."""
    candidate = await find_stitch_candidate(db, user_id, game_id)
    if candidate is not None:
        candidate.status = SessionStatus.ONGOING
        candidate.end_time = None
        candidate.duration_seconds = None
        candidate.is_flicker = False
        await db.commit()
        await db.refresh(candidate)
        logger.info("Session RESUMED user=%s game_id=%d session_id=%d", user_id, game_id, candidate.id)
        return candidate
    return await start_session(db, user_id, game_id)


async def error_session(db: AsyncSession, session: GameSession, notes: str) -> GameSession:
    """Transition ONGOING → ERROR with an explanatory note."""
    session.status = SessionStatus.ERROR
    session.notes = notes
    await db.commit()
    await db.refresh(session)
    logger.warning("Session ERROR session_id=%d notes=%r", session.id, notes)
    return session
