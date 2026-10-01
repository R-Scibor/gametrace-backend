"""
Database operations used by the Discord bot.
All functions accept an AsyncSession and perform a single logical operation.
"""
import logging
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.dml import Update

from app.bot.flicker_policy import find_stitch_candidate, is_short_flicker
from app.models.game import EnrichmentStatus, Game, GameAlias
from app.models.session import GameSession, SessionSource, SessionStatus
from app.models.user import User
from app.services.game_aliases import AliasResult, add_alias
from app.services.game_review import ensure_inbox_for_user
from app.services.session_overlap import is_session_overlap

logger = logging.getLogger(__name__)


class _AliasOwned(Exception):
    """The process name already belongs to a game. Roll back the new stub."""


async def _claim(db: AsyncSession, stmt: Update) -> bool:
    """Conditional UPDATE that does not mark the in-memory row dirty.

    ``synchronize_session="evaluate"`` would dirty an object whose loaded
    status still matches the WHERE, and the following commit would write that
    stale status even when the database matched zero rows.
    """
    result = await db.execute(
        stmt.returning(GameSession.id),
        execution_options={"synchronize_session": False},
    )
    return result.scalar_one_or_none() is not None


async def get_user_if_tracked(db: AsyncSession, discord_id: str) -> User | None:
    """
    Return User only if they have already logged into the app (exist in users
    table) and are not scheduled for account deletion.
    """
    user = await db.get(User, discord_id)
    if user is not None and user.purge_at is not None:
        return None
    return user


async def resolve_alias(db: AsyncSession, process_name: str) -> int | None:
    """Return the game id for this exact Discord process name, or None.

    The match is case-sensitive. A second alias of the same game returns that
    game's id. A miss does not insert a stub.
    """
    result = await db.execute(
        select(GameAlias.game_id).where(GameAlias.discord_process_name == process_name)
    )
    return result.scalar_one_or_none()


async def get_or_create_game(db: AsyncSession, process_name: str) -> tuple[Game, bool]:
    """Look up a game by Discord process name. Insert one stub on a miss.

    ``created`` is True only when this call inserted the alias. A concurrent
    insert rolls this call's stub back and returns the owner's row.
    The created stub is committed here so the current presence and startup
    callers, which do not commit this write themselves, still persist it.
    """
    game_id = await resolve_alias(db, process_name)
    if game_id is not None:
        game = await db.get(Game, game_id)
        assert game is not None
        return game, False

    owner_id: int | None = None
    created: Game | None = None
    try:
        async with db.begin_nested():
            game = Game(primary_name=process_name)
            db.add(game)
            await db.flush()
            result, found_owner = await add_alias(db, game.id, process_name)
            if result is not AliasResult.CREATED:
                owner_id = found_owner
                raise _AliasOwned()
            created = game
    except _AliasOwned:
        owner = await db.get(Game, owner_id) if owner_id is not None else None
        if owner is None:
            resolved = await resolve_alias(db, process_name)
            owner = await db.get(Game, resolved) if resolved is not None else None
        if owner is None:
            raise
        return owner, False

    assert created is not None
    logger.info("Created stub game %r (id=%d)", process_name, created.id)
    await db.commit()
    return created, True


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


async def start_session(db: AsyncSession, user_id: str, game_id: int) -> GameSession | None:
    """Create a new ONGOING BOT session.

    Returns None when the user row is missing or ``purge_at`` is set, and when
    the exclusion constraint says this instant is already covered. A live
    ONGOING for this ``game_id`` is returned. A live ONGOING for a different
    game is re-raised. This function flushes and does not commit.
    """
    user = await db.get(User, user_id)
    if user is None or user.purge_at is not None:
        logger.info(
            "Session start skipped user=%s; account missing or scheduled for deletion",
            user_id,
        )
        return None
    game = await db.get(Game, game_id)
    try:
        async with db.begin_nested():
            session = GameSession(
                user_id=user_id,
                game_id=game_id,
                start_time=datetime.now(UTC),
                status=SessionStatus.ONGOING,
                source=SessionSource.BOT,
            )
            db.add(session)
            await db.flush()
    except IntegrityError as exc:
        existing = await get_ongoing_session(db, user_id)
        if existing is not None and existing.game_id == game_id:
            logger.warning(
                "ONGOING insert raced for user=%s; returning session_id=%d",
                user_id,
                existing.id,
            )
            return existing
        if existing is not None:
            raise
        if is_session_overlap(exc):
            logger.info(
                "Session start skipped user=%s; this instant is already covered",
                user_id,
            )
            return None
        raise
    else:
        if game is not None and game.enrichment_status == EnrichmentStatus.NEEDS_REVIEW:
            await ensure_inbox_for_user(db, game_id, user_id)
            await db.flush()
        logger.info(
            "Session STARTED user=%s game_id=%d session_id=%d",
            user_id,
            game_id,
            session.id,
        )
        return session


async def complete_session(db: AsyncSession, session: GameSession) -> GameSession:
    """Transition ONGOING → COMPLETED, fill end_time and duration.

    Zero rows means the row is no longer a live ONGOING session. The returned
    object is the database row, not the caller's stale status.
    """
    now = datetime.now(UTC)
    duration = int((now - session.start_time).total_seconds())
    flicker = session.source == SessionSource.BOT and is_short_flicker(duration)
    claimed = await _claim(
        db,
        update(GameSession)
        .where(
            GameSession.id == session.id,
            GameSession.status == SessionStatus.ONGOING,
            GameSession.deleted_at.is_(None),
        )
        .values(
            status=SessionStatus.COMPLETED,
            end_time=now,
            duration_seconds=duration,
            is_flicker=flicker,
        ),
    )
    if claimed:
        logger.info("Session COMPLETED session_id=%d duration=%ds", session.id, duration)
    else:
        logger.info("Session COMPLETE skipped session_id=%d; status changed", session.id)
    await db.refresh(session)
    return session


async def start_or_resume_session(db: AsyncSession, user_id: str, game_id: int) -> GameSession | None:
    """Reopen a recent same-game BOT session if within the stitch window, else start fresh.

    A candidate the user has since edited, trashed, or otherwise moved off
    ``COMPLETED`` / ``BOT`` is left alone and a new session is started instead.
    """
    candidate = await find_stitch_candidate(db, user_id, game_id)
    if candidate is not None:
        candidate_id = candidate.id
        try:
            async with db.begin_nested():
                claimed = await _claim(
                    db,
                    update(GameSession)
                    .where(
                        GameSession.id == candidate_id,
                        GameSession.status == SessionStatus.COMPLETED,
                        GameSession.source == SessionSource.BOT,
                        GameSession.deleted_at.is_(None),
                    )
                    .values(
                        status=SessionStatus.ONGOING,
                        end_time=None,
                        duration_seconds=None,
                        is_flicker=False,
                    ),
                )
        except IntegrityError as exc:
            if not is_session_overlap(exc):
                raise
            logger.info(
                "Session resume skipped session_id=%d; range already covered",
                candidate_id,
            )
        else:
            if claimed:
                await db.refresh(candidate)
                logger.info(
                    "Session RESUMED user=%s game_id=%d session_id=%d",
                    user_id,
                    game_id,
                    candidate_id,
                )
                return candidate
            logger.info("Session resume skipped session_id=%d; row changed", candidate_id)
    return await start_session(db, user_id, game_id)


async def error_session(db: AsyncSession, session: GameSession, notes: str) -> GameSession:
    """Transition a live ONGOING session to ERROR with an explanatory note.

    Zero rows means another writer already moved the row. The returned object
    is the database row, so a completed session is not stamped ERROR.
    """
    claimed = await _claim(
        db,
        update(GameSession)
        .where(
            GameSession.id == session.id,
            GameSession.status == SessionStatus.ONGOING,
            GameSession.deleted_at.is_(None),
        )
        .values(status=SessionStatus.ERROR, notes=notes),
    )
    if claimed:
        logger.warning("Session ERROR session_id=%d notes=%r", session.id, notes)
    else:
        logger.warning("Session ERROR skipped session_id=%d; status changed", session.id)
    await db.refresh(session)
    return session
