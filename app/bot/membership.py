"""Keeps the Redis bot-visibility snapshot in sync with the guild cache.

Only registered GameTrace users are written (see ``app.core.bot_membership``).
Rebuild cost is O(users x guilds) cache lookups, independent of guild size.
Every function swallows and logs errors: visibility is advisory and must never
break presence tracking or a slash command.
"""

import asyncio
import logging
import time
from collections.abc import Sequence

import discord
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.bot_membership import (
    VISIBLE_USERS_KEY,
    add_visible,
    remove_visible,
    write_snapshot,
)
from app.models.user import User
from app.services.demo import DEMO_DISCORD_ID

logger = logging.getLogger(__name__)


# Serializes every snapshot writer in this (single) bot process: full
# rebuilds against each other and against incremental join/remove/refresh, so
# an older scan can never overwrite a newer change.
_lock = asyncio.Lock()


def _guild_complete(g) -> bool:
    return getattr(g, "chunked", True) and not getattr(g, "unavailable", False)


def is_visible(
    guilds: Sequence[discord.Guild], discord_id: str, *, exclude_guild_id: int | None = None
) -> bool:
    uid = int(discord_id)
    return any(
        g.get_member(uid) is not None for g in guilds if g.id != exclude_guild_id
    )


async def _is_registered(db: AsyncSession, discord_id: str) -> bool:
    if discord_id == DEMO_DISCORD_ID:
        return False
    return await db.get(User, discord_id) is not None


async def rebuild_snapshot(db: AsyncSession, redis, guilds: Sequence[discord.Guild]) -> int:
    """Full rebuild from the DB user list. Returns the visible-user count.

    If any guild is unavailable or not chunked the cache cannot prove
    absence: the snapshot is marked partial (API answers null for anyone not
    in the set) and previously visible ids are kept (add-only).
    """
    async with _lock:
        scan_started_at = int(time.time())
        result = await db.execute(
            select(User.discord_id).where(User.discord_id != DEMO_DISCORD_ID)
        )
        user_ids = list(result.scalars())
        guilds = list(guilds)
        # No guilds at all cannot prove anything (e.g. cache not populated).
        complete = bool(guilds) and all(_guild_complete(g) for g in guilds)
        previous: set[str] = set() if complete else set(await redis.smembers(VISIBLE_USERS_KEY))
        visible = {uid for uid in user_ids if uid.isdigit() and is_visible(guilds, uid)}
        visible |= previous & set(user_ids)
        await write_snapshot(redis, visible, complete=complete, scan_started_at=scan_started_at)
    logger.info(
        "bot_membership.snapshot_rebuilt visible=%d guilds=%d complete=%s",
        len(visible), len(guilds), complete,
    )
    return len(visible)


async def on_member_join(db: AsyncSession, redis, member: discord.abc.User) -> None:
    discord_id = str(member.id)
    try:
        if await _is_registered(db, discord_id):
            async with _lock:
                await add_visible(redis, discord_id)
    except Exception:
        logger.warning("bot_membership.member_join_failed", exc_info=True)


async def on_member_remove(
    redis, guilds: Sequence[discord.Guild], member: discord.Member
) -> None:
    """Remove only when every other guild's cache is complete and none has
    the member; a cache miss in a partial guild proves nothing."""
    discord_id = str(member.id)
    try:
        async with _lock:
            others = [g for g in guilds if g.id != member.guild.id]
            if not all(_guild_complete(g) for g in others):
                return
            if is_visible(others, discord_id):
                return
            # SREM of an id that is not in the set is a no-op, so no DB lookup
            # is needed to filter out unregistered members here.
            await remove_visible(redis, discord_id)
    except Exception:
        logger.warning("bot_membership.member_remove_failed", exc_info=True)


async def refresh_user(redis, client: discord.Client, discord_id: str) -> None:
    """Add a just-registered/linked user if the bot sees them. Add-only: a
    miss here never removes (the API reports null until the next full scan
    covers the new account)."""
    if discord_id == DEMO_DISCORD_ID:
        return
    try:
        if is_visible(client.guilds, discord_id):
            async with _lock:
                await add_visible(redis, discord_id)
    except Exception:
        logger.warning("bot_membership.refresh_user_failed", exc_info=True)


async def on_guild_join(guild: discord.Guild, rebuild) -> None:
    """Chunk a runtime-joined guild, then rebuild.

    The rebuild runs even if chunking fails: the unchunked guild then marks
    the snapshot partial, so its members read as null instead of a stale
    "complete" snapshot reporting them as false. The periodic rebuild retries.
    """
    if not guild.chunked:
        try:
            await guild.chunk()
        except Exception:
            logger.warning("bot_membership.chunk_failed guild=%s", guild.id, exc_info=True)
    await rebuild()
