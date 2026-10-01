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

from app.bot.activity import activity_name
from app.bot.reconcile import reconcile_user
from app.bot.session_lock import user_session_lock
from app.models.session import GameSession, SessionSource, SessionStatus

logger = logging.getLogger(__name__)


def _find_member(guilds: Sequence[discord.Guild], discord_id: str) -> discord.Member | None:
    uid = int(discord_id)
    for guild in guilds:
        member = guild.get_member(uid)
        if member:
            return member
    return None


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
    if not ongoing_sessions:
        logger.info("Self-Healing: no ONGOING sessions found, nothing to do.")
        return

    logger.info("Self-Healing: found %d ONGOING session(s)", len(ongoing_sessions))
    user_ids = [row.user_id for row in ongoing_sessions]
    for user_id in user_ids:
        async with user_session_lock(db, user_id):
            member = _find_member(guilds, user_id)
            await reconcile_user(
                db,
                user_id,
                before_name=None,
                after_name=activity_name(member) if member is not None else None,
                gap=True,
                member_found=member is not None,
                now=now,
            )
    logger.info("Self-Healing: reconciliation complete.")
