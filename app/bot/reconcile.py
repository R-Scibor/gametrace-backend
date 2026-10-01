"""What an observed activity means for the user's open session.

Callers hold ``user_session_lock``. This function does not acquire it.
It re-reads the user and the live ONGOING row, flushes through the session
helpers, and commits. A close commits before the start that follows it.
"""

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.session_manager import (
    complete_session,
    error_session,
    get_ongoing_session,
    get_or_create_game,
    resolve_alias,
    start_or_resume_session,
    start_session,
)
from app.models.game import EnrichmentStatus, Game
from app.models.session import GameSession
from app.models.user import User
from app.services.enrichment_dispatch import queue_enrichment

logger = logging.getLogger(__name__)

STALE_SESSION_HOURS = 12

_SCHEDULED_NOTE = "Self-Healing: account scheduled for deletion."
_MISSING_MEMBER_NOTE = "Self-Healing: user not found in any guild after bot restart."
_IDLE_NOTE = "Self-Healing: bot restarted, player is no longer in-game."


def _enqueue(game_id: int) -> None:
    try:
        queue_enrichment(game_id)
    except Exception:
        logger.exception("Failed to queue enrichment for game_id=%d", game_id)


async def _prior_name(db: AsyncSession, game_id: int) -> str:
    game = await db.get(Game, game_id)
    if game is None or not game.primary_name:
        return "unknown"
    return game.primary_name


async def _error_and_commit(db: AsyncSession, ongoing: GameSession, note: str) -> None:
    await error_session(db, ongoing, note)
    await db.commit()


async def _complete_and_commit(db: AsyncSession, ongoing: GameSession) -> None:
    await complete_session(db, ongoing)
    await db.commit()


async def _start_and_commit(
    db: AsyncSession,
    user_id: str,
    process_name: str,
    *,
    startup: bool,
) -> None:
    game, _created = await get_or_create_game(db, process_name)
    game_id = game.id
    pending = game.enrichment_status == EnrichmentStatus.PENDING
    try:
        if startup:
            started = await start_session(db, user_id, game_id)
        else:
            started = await start_or_resume_session(db, user_id, game_id)
    except IntegrityError:
        await db.rollback()
        raise
    if started is None:
        await db.rollback()
        return
    await db.commit()
    if pending:
        _enqueue(game_id)


def _presence_note(name: str) -> str:
    return f"Presence: open session did not match activity {name!r}."


def _open_row_continues(
    ongoing: GameSession | None,
    before_name: str | None,
    before_id: int | None,
    after_id: int | None,
) -> bool:
    """True when the open row is already the continuation of the game now playing.

    The ids matching is not enough. The row continues only when there was no
    previous activity, or that activity resolves to this same game. A different
    game that just closed means the open row was never that session.
    """
    if ongoing is None or after_id is None or ongoing.game_id != after_id:
        return False
    if before_name is None:
        return True
    return before_id == after_id


async def _close_open(
    db: AsyncSession,
    ongoing: GameSession,
    before_id: int | None,
    note: str,
) -> None:
    if before_id is not None and ongoing.game_id == before_id:
        await _complete_and_commit(db, ongoing)
        return
    await _error_and_commit(db, ongoing, note)


async def _live(
    db: AsyncSession,
    user_id: str,
    before_name: str | None,
    after_name: str | None,
    ongoing: GameSession | None,
) -> None:
    before_id = await resolve_alias(db, before_name) if before_name else None
    after_id = await resolve_alias(db, after_name) if after_name else None

    if after_name is None:
        if ongoing is None:
            return
        await _close_open(db, ongoing, before_id, _presence_note(before_name or ""))
        return

    if _open_row_continues(ongoing, before_name, before_id, after_id):
        return

    if ongoing is not None:
        closed_name = after_name if before_name is None else before_name
        await _close_open(db, ongoing, before_id, _presence_note(closed_name))
    await _start_and_commit(db, user_id, after_name, startup=False)


async def _startup(
    db: AsyncSession,
    user_id: str,
    ongoing: GameSession,
    after_name: str | None,
    member_found: bool,
    now: datetime,
) -> None:
    if not member_found:
        await _error_and_commit(db, ongoing, _MISSING_MEMBER_NOTE)
        return

    after_id = await resolve_alias(db, after_name) if after_name else None
    if after_name and after_id == ongoing.game_id:
        logger.info(
            "Self-Healing: session_id=%d continues (same game_id=%d)",
            ongoing.id,
            ongoing.game_id,
        )
        return

    if after_name:
        prior = await _prior_name(db, ongoing.game_id)
        session_id = ongoing.id
        await _error_and_commit(
            db,
            ongoing,
            f"Self-Healing: bot restarted, player switched from {prior!r} to {after_name!r}.",
        )
        logger.info(
            "Self-Healing: session_id=%d ERROR, new session started for %r",
            session_id,
            after_name,
        )
        await _start_and_commit(db, user_id, after_name, startup=True)
        return

    age = now - ongoing.start_time.astimezone(UTC)
    if age > timedelta(hours=STALE_SESSION_HOURS):
        session_id = ongoing.id
        await _error_and_commit(
            db,
            ongoing,
            (
                f"Self-Healing: session exceeded {STALE_SESSION_HOURS}h threshold "
                "after bot restart — possible stale session."
            ),
        )
        logger.warning("Self-Healing: session_id=%d marked ERROR (>12h stale)", session_id)
        return

    await _error_and_commit(db, ongoing, _IDLE_NOTE)


async def reconcile_user(
    db: AsyncSession,
    user_id: str,
    *,
    before_name: str | None,
    after_name: str | None,
    gap: bool,
    member_found: bool = True,
    now: datetime | None = None,
) -> None:
    """Apply one observed activity to the user's live ONGOING row.

    ``gap=False`` is a live presence event. ``gap=True`` is a real process
    start. ``now`` is the clock for the startup age check; the scanner passes
    one value for the whole pass. Live presence does not use it.
    """
    if now is None:
        now = datetime.now(UTC)
    user = await db.get(User, user_id, populate_existing=True)
    ongoing = await get_ongoing_session(db, user_id, populate_existing=True)
    if user is None or user.purge_at is not None:
        if ongoing is not None:
            await _error_and_commit(db, ongoing, _SCHEDULED_NOTE)
        return
    if gap:
        if ongoing is None:
            return
        await _startup(db, user_id, ongoing, after_name, member_found, now)
        return
    await _live(db, user_id, before_name, after_name, ongoing)
