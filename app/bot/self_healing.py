"""
Self-Healing: runs once on bot startup to reconcile all ONGOING sessions.

Logic per session:
  - Find member in any guild.
  - Member is playing the SAME game → keep ONGOING (unless >12h → ERROR).
  - Member is playing a DIFFERENT game → ERROR + start new session for new game.
  - Member is not playing / not found → ERROR.
"""
import logging
from datetime import datetime, timedelta, timezone

import discord
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.session_manager import error_session, get_or_create_game, start_session
from app.models.session import GameSession, SessionStatus

logger = logging.getLogger(__name__)

STALE_SESSION_HOURS = 12


def _get_game_name(member: discord.Member) -> str | None:
    """Extract the currently played game name from a member's activities."""
    for activity in member.activities:
        if isinstance(activity, discord.Game):
            return activity.name
        if (
            isinstance(activity, discord.Activity)
            and activity.type == discord.ActivityType.playing
        ):
            return activity.name
    return None


def _find_member(guilds: list[discord.Guild], discord_id: str) -> discord.Member | None:
    uid = int(discord_id)
    for guild in guilds:
        member = guild.get_member(uid)
        if member:
            return member
    return None


async def run_self_healing(db: AsyncSession, guilds: list[discord.Guild]) -> None:
    logger.info("Self-Healing: starting reconciliation...")

    result = await db.execute(
        select(GameSession).where(
            GameSession.status == SessionStatus.ONGOING,
            GameSession.deleted_at.is_(None),
        )
    )
    ongoing_sessions: list[GameSession] = list(result.scalars().all())

    if not ongoing_sessions:
        logger.info("Self-Healing: no ONGOING sessions found, nothing to do.")
        return

    logger.info("Self-Healing: found %d ONGOING session(s)", len(ongoing_sessions))
    now = datetime.now(timezone.utc)

    for session in ongoing_sessions:
        member = _find_member(guilds, session.user_id)

        if member is None:
            await error_session(
                db,
                session,
                "Self-Healing: user not found in any guild after bot restart.",
            )
            continue

        current_game = _get_game_name(member)

        # Fetch the game name that was recorded for this session
        from app.models.game import Game  # avoid circular at module level
        game = await db.get(Game, session.game_id)
        session_game_name = game.primary_name if game else None

        # Check for stale session (>12h regardless of game)
        age = now - session.start_time.replace(tzinfo=timezone.utc)
        if age > timedelta(hours=STALE_SESSION_HOURS):
            await error_session(
                db,
                session,
                f"Self-Healing: session exceeded {STALE_SESSION_HOURS}h threshold after bot restart — possible stale session.",
            )
            logger.warning(
                "Self-Healing: session_id=%d marked ERROR (>12h stale)", session.id
            )
            continue

        if current_game and current_game == session_game_name:
            # Same game — session continues uninterrupted
            logger.info(
                "Self-Healing: session_id=%d continues (same game %r)", session.id, current_game
            )
        elif current_game and current_game != session_game_name:
            # Switched game — error old session, start new one
            await error_session(
                db,
                session,
                f"Self-Healing: bot restarted, player switched from {session_game_name!r} to {current_game!r}.",
            )
            new_game, _ = await get_or_create_game(db, current_game)
            await start_session(db, session.user_id, new_game.id)
            logger.info(
                "Self-Healing: session_id=%d ERROR, new session started for %r",
                session.id,
                current_game,
            )
        else:
            # Not playing anything
            await error_session(
                db,
                session,
                "Self-Healing: bot restarted, player is no longer in-game.",
            )

    logger.info("Self-Healing: reconciliation complete.")
