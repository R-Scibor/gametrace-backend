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
from datetime import UTC, datetime

from app.core.bot_heartbeat import (
    BOT_HEARTBEAT_KEY,
    BOT_STARTED_AT_KEY,
    HEARTBEAT_WINDOW_SECONDS,
)
from app.services.demo import DEMO_DISCORD_ID

logger = logging.getLogger(__name__)

VISIBLE_USERS_KEY = "bot:visible_users"
# Unix time taken *before* the rebuild's DB query: users created at or after
# this instant (minus a commit-lag margin) were not covered by the scan.
MEMBERSHIP_SYNCED_AT_KEY = "bot:membership_synced_at"
# "1" when the last full rebuild saw every guild available and chunked, so
# absence from the set proves the bot cannot see the user. "0" = add-only.
MEMBERSHIP_COMPLETE_KEY = "bot:membership_complete"
# A row whose transaction started shortly before the scan may commit after
# the scan's query; treat such users as not covered.
SCAN_COVERAGE_MARGIN_SECONDS = 60
_SADD_CHUNK = 1000


async def get_bot_visibility(
    redis, discord_id: str, created_at: datetime | None = None
) -> bool | None:
    """True when the bot sees the user, False only when the snapshot proves
    absence, None whenever it cannot (stale bot, partial cache, user newer
    than the last full scan, Redis error, demo account)."""
    if discord_id == DEMO_DISCORD_ID:
        return None
    try:
        # One round trip, so the freshness keys and the membership answer
        # come from the same moment.
        async with redis.pipeline(transaction=True) as pipe:
            pipe.mget(
                BOT_HEARTBEAT_KEY,
                BOT_STARTED_AT_KEY,
                MEMBERSHIP_SYNCED_AT_KEY,
                MEMBERSHIP_COMPLETE_KEY,
            )
            pipe.sismember(VISIBLE_USERS_KEY, discord_id)
            (heartbeat, started_at, synced_at, complete), is_member = await pipe.execute()
        if heartbeat is None or started_at is None or synced_at is None:
            return None
        if int(time.time()) - int(heartbeat) > HEARTBEAT_WINDOW_SECONDS:
            return None
        if int(synced_at) < int(started_at):
            return None
        if is_member:
            return True
        # Not in the set: only a complete scan that covered this user proves it.
        if complete != "1":
            return None
        if created_at is None:
            return None
        if created_at.tzinfo is None:  # column is timestamptz; be safe anyway
            created_at = created_at.replace(tzinfo=UTC)
        if created_at.timestamp() >= int(synced_at) - SCAN_COVERAGE_MARGIN_SECONDS:
            return None
        return False
    except Exception:
        logger.warning("bot_visibility.redis_read_failed", exc_info=True)
        return None


async def write_snapshot(
    redis, ids: Iterable[str], *, complete: bool, scan_started_at: int
) -> None:
    """Atomically replace the snapshot and its metadata (MULTI/EXEC).

    Queued client-side and sent in one await: no temp key can leak and no
    reader sees a half-written set. Callers in the bot must also hold
    ``app.bot.membership`` 's lock so writers do not overwrite each other.
    """
    ids = list(ids)
    async with redis.pipeline(transaction=True) as pipe:
        pipe.delete(VISIBLE_USERS_KEY)
        for i in range(0, len(ids), _SADD_CHUNK):
            pipe.sadd(VISIBLE_USERS_KEY, *ids[i : i + _SADD_CHUNK])
        pipe.set(MEMBERSHIP_COMPLETE_KEY, "1" if complete else "0")
        pipe.set(MEMBERSHIP_SYNCED_AT_KEY, int(scan_started_at))
        await pipe.execute()


async def add_visible(redis, discord_id: str) -> None:
    await redis.sadd(VISIBLE_USERS_KEY, discord_id)


async def remove_visible(redis, discord_id: str) -> None:
    await redis.srem(VISIBLE_USERS_KEY, discord_id)
