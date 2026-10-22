"""Redis snapshot of which registered GameTrace users the Discord bot can see.

The API process cannot read the bot's in-memory guild cache, so the bot
publishes a SET of registered ``discord_id`` values that are a member of at
least one guild the bot is in. Only registered users are stored, never the
full member list of a guild.

The snapshot is only trusted while the bot is alive (fresh heartbeat) and was
rebuilt by the *current* bot process (``synced_at >= started_at``). Anything
else is reported as unknown (``None``) so a dead bot or cold cache never makes
the web app claim the user is invisible.

Single-process bot assumption: with sharding, each shard would need its own
snapshot key and the reader would union them.
"""

import logging
import time
from collections.abc import Iterable

from app.core.bot_heartbeat import (
    BOT_HEARTBEAT_KEY,
    BOT_STARTED_AT_KEY,
    HEARTBEAT_WINDOW_SECONDS,
)
from app.services.demo import DEMO_DISCORD_ID

logger = logging.getLogger(__name__)

VISIBLE_USERS_KEY = "bot:visible_users"
MEMBERSHIP_SYNCED_AT_KEY = "bot:membership_synced_at"
_SADD_CHUNK = 1000


async def get_bot_visibility(redis, discord_id: str) -> bool | None:
    """True/False when the snapshot is trustworthy, None when unknown."""
    if discord_id == DEMO_DISCORD_ID:
        return None
    try:
        # One round trip, so the freshness keys and the membership answer
        # come from the same moment.
        async with redis.pipeline(transaction=True) as pipe:
            pipe.mget(BOT_HEARTBEAT_KEY, BOT_STARTED_AT_KEY, MEMBERSHIP_SYNCED_AT_KEY)
            pipe.sismember(VISIBLE_USERS_KEY, discord_id)
            (heartbeat, started_at, synced_at), is_member = await pipe.execute()
        if heartbeat is None or started_at is None or synced_at is None:
            return None
        if int(time.time()) - int(heartbeat) > HEARTBEAT_WINDOW_SECONDS:
            return None
        if int(synced_at) < int(started_at):
            return None
        return bool(is_member)
    except Exception:
        logger.warning("bot_visibility.redis_read_failed", exc_info=True)
        return None


async def write_snapshot(redis, ids: Iterable[str]) -> None:
    """Atomically replace the snapshot and stamp ``synced_at`` (MULTI/EXEC).

    The whole replacement is queued client-side and sent in one await, so no
    incremental add/remove from another bot task can interleave between the
    caller's cache scan and the swap, and no temp key can leak on a crash.
    """
    ids = list(ids)
    async with redis.pipeline(transaction=True) as pipe:
        pipe.delete(VISIBLE_USERS_KEY)
        for i in range(0, len(ids), _SADD_CHUNK):
            pipe.sadd(VISIBLE_USERS_KEY, *ids[i : i + _SADD_CHUNK])
        pipe.set(MEMBERSHIP_SYNCED_AT_KEY, int(time.time()))
        await pipe.execute()


async def add_visible(redis, discord_id: str) -> None:
    await redis.sadd(VISIBLE_USERS_KEY, discord_id)


async def remove_visible(redis, discord_id: str) -> None:
    await redis.srem(VISIBLE_USERS_KEY, discord_id)
