"""Keeps the Redis bot-visibility snapshot in sync with the guild cache.

Only registered GameTrace users are written (see ``app.core.bot_membership``).
Rebuild cost is O(users x guilds) cache lookups, independent of guild size.
Every function swallows and logs errors: visibility is advisory and must never
break presence tracking or a slash command.
"""

import logging
from collections.abc import Sequence

import discord
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.bot_membership import add_visible, remove_visible, write_snapshot
from app.models.user import User
from app.services.demo import DEMO_DISCORD_ID

logger = logging.getLogger(__name__)


def is_visible(guilds: Sequence[discord.Guild], discord_id: str, *, exclude_guild_id: int | None = None) -> bool:
    uid = int(discord_id)
    return any(
        g.get_member(uid) is not None for g in guilds if g.id != exclude_guild_id
    )


async def _is_registered(db: AsyncSession, discord_id: str) -> bool:
    if discord_id == DEMO_DISCORD_ID:
        return False
    return await db.get(User, discord_id) is not None


async def rebuild_snapshot(db: AsyncSession, redis, guilds: Sequence[discord.Guild]) -> int:
    """Full rebuild from the DB user list. Returns the visible-user count."""
    result = await db.execute(select(User.discord_id).where(User.discord_id != DEMO_DISCORD_ID))
    visible = {uid for uid in result.scalars() if uid.isdigit() and is_visible(guilds, uid)}
    await write_snapshot(redis, visible)
    logger.info("bot_membership.snapshot_rebuilt visible=%d guilds=%d", len(visible), len(guilds))
    return len(visible)


async def on_member_join(db: AsyncSession, redis, member: discord.abc.User) -> None:
    discord_id = str(member.id)
    try:
        if await _is_registered(db, discord_id):
            await add_visible(redis, discord_id)
    except Exception:
        logger.warning("bot_membership.member_join_failed", exc_info=True)


async def on_member_remove(
    db: AsyncSession, redis, guilds: Sequence[discord.Guild], member: discord.Member
) -> None:
    discord_id = str(member.id)
    try:
        if not await _is_registered(db, discord_id):
            return
        if is_visible(guilds, discord_id, exclude_guild_id=member.guild.id):
            return
        await remove_visible(redis, discord_id)
    except Exception:
        logger.warning("bot_membership.member_remove_failed", exc_info=True)


async def refresh_user(redis, client: discord.Client, discord_id: str) -> None:
    """Re-check one user (after /register or /login created the row)."""
    if discord_id == DEMO_DISCORD_ID:
        return
    try:
        if is_visible(client.guilds, discord_id):
            await add_visible(redis, discord_id)
        else:
            await remove_visible(redis, discord_id)
    except Exception:
        logger.warning("bot_membership.refresh_user_failed", exc_info=True)
