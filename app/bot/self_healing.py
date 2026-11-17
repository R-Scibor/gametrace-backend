"""Startup reconciliation. Live presence uses the same ``reconcile_user``.

Runs once per process, on the first successful ``on_ready``. The candidate
list is taken before the per-user lock. ``reconcile_user`` re-reads inside
the lock and skips a row that is no longer a live ONGOING.
"""

import logging
from collections.abc import Sequence
from datetime import UTC, datetime

import discord
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.activity import playing_activity, playing_started_at
from app.bot.reconcile import reconcile_user
from app.bot.session_lock import user_session_lock
from app.models.session import GameSession, SessionSource, SessionStatus
from app.models.user import User
from app.services.demo import DEMO_DISCORD_ID

logger = logging.getLogger(__name__)


def _find_member(guilds: Sequence[discord.Guild], discord_id: str) -> discord.Member | None:
    uid = int(discord_id)
    for guild in guilds:
        member = guild.get_member(uid)
        if member:
            return member
    return None


def _playing_members(guilds: Sequence[discord.Guild]) -> dict[str, discord.Member]:
    """Members with a playing activity, one entry per Discord id across guilds."""
    found: dict[str, discord.Member] = {}
    for guild in guilds:
        for member in guild.members:
            if member.bot or str(member.id) in found:
                continue
            if playing_activity(member) is not None:
                found[str(member.id)] = member
    return found


async def _reconcile_startup(
    db: AsyncSession,
    guilds: Sequence[discord.Guild],
    user_id: str,
    now: datetime,
    *,
    start_missing: bool = False,
) -> None:
    async with user_session_lock(db, user_id):
        member = _find_member(guilds, user_id)
        found = playing_activity(member) if member is not None else None
        await reconcile_user(
            db,
            user_id,
            before_name=None,
            before_application_id=None,
            after_name=found[0] if found is not None else None,
            after_application_id=found[1] if found is not None else None,
            after_started_at=playing_started_at(member) if member is not None else None,
            gap=True,
            member_found=member is not None,
            now=now,
            start_missing=start_missing,
        )


async def run_self_healing(db: AsyncSession, guilds: Sequence[discord.Guild]) -> None:
    logger.info("Self-Healing: starting reconciliation...")
    now = datetime.now(UTC)
    result = await db.execute(
        select(GameSession)
        .where(
            GameSession.source == SessionSource.BOT,
            GameSession.status == SessionStatus.ONGOING,
            GameSession.deleted_at.is_(None),
        )
        .order_by(GameSession.id)
    )
    ongoing_sessions = list(result.scalars().all())
    logger.info("Self-Healing: found %d ONGOING session(s)", len(ongoing_sessions))
    user_ids = [row.user_id for row in ongoing_sessions]
    for user_id in user_ids:
        await _reconcile_startup(db, guilds, user_id, now)

    # Players who started a game while the bot was down have no row at all, and
    # live presence will not report the game again until it changes.
    playing = _playing_members(guilds)
    known = set(user_ids)
    candidates = [uid for uid in playing if uid not in known and uid != DEMO_DISCORD_ID]
    if candidates:
        registered = await db.execute(select(User.discord_id).where(User.discord_id.in_(candidates)))
        missed = sorted(set(registered.scalars()))
        logger.info("Self-Healing: %d player(s) in game with no ONGOING session", len(missed))
        for user_id in missed:
            await _reconcile_startup(db, guilds, user_id, now, start_missing=True)
    logger.info("Self-Healing: reconciliation complete.")
