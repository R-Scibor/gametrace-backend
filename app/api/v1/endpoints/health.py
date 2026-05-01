import logging
import time
from typing import Any

import redis.asyncio as redis_async
from fastapi import APIRouter

from app.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter()

BOT_STARTED_AT_KEY = "bot:started_at"
BOT_HEARTBEAT_KEY = "bot:heartbeat"
HEARTBEAT_STALE_AFTER_SECONDS = 90

API_STARTED_AT = int(time.time())

_redis: redis_async.Redis | None = None


def _get_redis() -> redis_async.Redis:
    global _redis
    if _redis is None:
        _redis = redis_async.from_url(settings.redis_url, decode_responses=True)
    return _redis


@router.get("")
async def health() -> dict[str, Any]:
    now = int(time.time())

    bot: dict[str, Any]
    try:
        r = _get_redis()
        started_at_raw = await r.get(BOT_STARTED_AT_KEY)
        heartbeat_raw = await r.get(BOT_HEARTBEAT_KEY)

        started_at = int(started_at_raw) if started_at_raw else None
        heartbeat = int(heartbeat_raw) if heartbeat_raw else None

        if heartbeat is not None and (now - heartbeat) <= HEARTBEAT_STALE_AFTER_SECONDS:
            bot_status = "online"
        else:
            bot_status = "offline"

        bot = {
            "status": bot_status,
            "uptime_seconds": (now - started_at) if started_at else None,
            "last_heartbeat_seconds_ago": (now - heartbeat) if heartbeat else None,
        }
    except Exception:
        logger.warning("Health check could not reach Redis", exc_info=True)
        bot = {
            "status": "unknown",
            "uptime_seconds": None,
            "last_heartbeat_seconds_ago": None,
        }

    return {
        "status": "ok",
        "version": settings.app_version,
        "commit_sha": settings.git_sha,
        "build_time": settings.build_time,
        "api": {"uptime_seconds": now - API_STARTED_AT},
        "bot": bot,
    }
