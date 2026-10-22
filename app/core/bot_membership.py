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
import uuid
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
        heartbeat, started_at, synced_at = await redis.mget(
            BOT_HEARTBEAT_KEY, BOT_STARTED_AT_KEY, MEMBERSHIP_SYNCED_AT_KEY
        )
        if heartbeat is None or started_at is None or synced_at is None:
            return None
        if int(time.time()) - int(heartbeat) > HEARTBEAT_WINDOW_SECONDS:
            return None
        if int(synced_at) < int(started_at):
            return None
        return bool(await redis.sismember(VISIBLE_USERS_KEY, discord_id))
    except Exception:
        logger.warning("bot_visibility.redis_read_failed", exc_info=True)
        return None


async def write_snapshot(redis, ids: Iterable[str]) -> None:
    """Atomically replace the snapshot, then stamp ``synced_at``."""
    ids = list(ids)
    if ids:
        tmp = f"{VISIBLE_USERS_KEY}:tmp:{uuid.uuid4().hex}"
        try:
            for i in range(0, len(ids), _SADD_CHUNK):
                await redis.sadd(tmp, *ids[i : i + _SADD_CHUNK])
            await redis.rename(tmp, VISIBLE_USERS_KEY)
        except Exception:
            await redis.delete(tmp)
            raise
    else:
        await redis.delete(VISIBLE_USERS_KEY)
    await redis.set(MEMBERSHIP_SYNCED_AT_KEY, int(time.time()))


async def add_visible(redis, discord_id: str) -> None:
    await redis.sadd(VISIBLE_USERS_KEY, discord_id)


async def remove_visible(redis, discord_id: str) -> None:
    await redis.srem(VISIBLE_USERS_KEY, discord_id)
