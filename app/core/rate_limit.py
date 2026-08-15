"""Per-user hourly quotas for endpoints that spend a limited resource.

Keyed on ``user_id``, never on the bearer credential. Every login path issues a
NEW token and none are revoked on re-login, so a token-keyed counter was
resettable at will (log in again) and multipliable (hold N tokens for N budgets).

This is a plain async helper rather than a slowapi limiter because slowapi's key
function receives only the Request — the User resolved by ``get_current_user``
is not visible to it, which is exactly what forced token-keying before. Callers
invoke this inside the handler, where the user is already resolved.

Redis storage → shared across api workers and durable across restarts. Counts
ATTEMPTS: the increment happens before the paid/limited work, so a loop of
failing calls is bounded too.

Fails open on Redis errors: an outage must not take these endpoints down. The
paid voice endpoint layers a DB-backed daily cap on top for that reason — see
app/services/voice_quota.py.
"""
import logging

import redis.exceptions

from app.core.redis import get_redis

logger = logging.getLogger(__name__)

HOURLY_WINDOW_SECONDS = 3600


def hourly_key(bucket: str, user_id: str) -> str:
    return f"quota:{bucket}:{user_id}:h"


async def check_hourly_quota(user_id: str, bucket: str, limit: int) -> int | None:
    """Consume one unit of *bucket* for *user_id*.

    Returns Retry-After seconds when the user is over *limit* for the current
    hour, else None. Call once per request, immediately before the limited work.
    """
    try:
        r = get_redis()
        key = hourly_key(bucket, user_id)
        used = await r.incr(key)
        if used == 1:
            await r.expire(key, HOURLY_WINDOW_SECONDS)
        if used > limit:
            return max(1, await r.ttl(key))
    except (redis.exceptions.RedisError, ConnectionError, OSError):
        logger.warning(
            "rate_limit.redis_unavailable_failing_open bucket=%s", bucket, exc_info=True
        )
    return None
