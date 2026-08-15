"""Per-user quota for the paid voice endpoint (POST /voice/transcribe).

Keyed on ``user_id``, never on the bearer credential. Every login path issues a
NEW token, and nothing revokes the old ones, so a token-keyed counter was
resettable at will (log in again) and multipliable (hold N tokens for N budgets).
The endpoint resolves the User via get_current_user before this runs, so the
user id is available here — which is why the check lives in the handler rather
than in a slowapi key function (those see only the Request).

Two windows, two jobs:

* **Daily** bounds the bill. Counted from ``voice_usage``, which already stores
  one row per SUCCESSFUL transcription with ``user_id`` and ``created_at``
  indexed — so it is a cheap indexed count that survives a Redis flush. The
  usage insert is deliberately best-effort in the endpoint, so this count is a
  floor, not an exact ledger; the hourly counter covers the gap.
* **Hourly** bounds burst. A Redis counter incremented on every ATTEMPT, so a
  loop of failing calls (which writes no ``voice_usage`` row) is still capped.

Daily is checked first and is read-only: a daily-blocked request spends no money
and so must not consume hourly budget.
"""
import logging
from datetime import datetime, timedelta, timezone

import redis.exceptions
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis import get_redis
from app.models.voice_usage import VoiceUsage

logger = logging.getLogger(__name__)

HOURLY_LIMIT = 8
DAILY_LIMIT = 40
HOURLY_WINDOW_SECONDS = 3600
DAILY_WINDOW = timedelta(days=1)


def hourly_key(user_id: str) -> str:
    return f"voice:quota:{user_id}:h"


async def check_voice_quota(db: AsyncSession, user_id: str) -> int | None:
    """Return Retry-After seconds when the user is over quota, else None.

    Consumes one unit of the hourly budget as a side effect when the daily cap
    still has room — call this once per request, immediately before the paid
    work.
    """
    now = datetime.now(timezone.utc)

    daily_used, oldest = (
        await db.execute(
            select(func.count(), func.min(VoiceUsage.created_at)).where(
                VoiceUsage.user_id == user_id,
                VoiceUsage.created_at > now - DAILY_WINDOW,
            )
        )
    ).one()
    if daily_used >= DAILY_LIMIT:
        # Budget frees up when the oldest counted call leaves the 24h window.
        remaining = (oldest + DAILY_WINDOW) - now
        return max(1, int(remaining.total_seconds()) + 1)

    # Fails open: a Redis outage must not take voice down. The daily cap above
    # is DB-backed and still binds, so spend stays bounded either way.
    try:
        r = get_redis()
        key = hourly_key(user_id)
        hourly_used = await r.incr(key)
        if hourly_used == 1:
            await r.expire(key, HOURLY_WINDOW_SECONDS)
        if hourly_used > HOURLY_LIMIT:
            return max(1, await r.ttl(key))
    except (redis.exceptions.RedisError, ConnectionError, OSError):
        logger.warning("voice.quota.redis_unavailable_failing_open", exc_info=True)

    return None
