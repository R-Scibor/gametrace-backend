import logging
import time
from typing import Any

from fastapi import APIRouter

from app.core.bot_heartbeat import (
    BOT_HEARTBEAT_KEY,
    BOT_STARTED_AT_KEY,
    HEARTBEAT_WINDOW_SECONDS,
)
from app.core.config import settings
from app.core.redis import get_redis

logger = logging.getLogger(__name__)

router = APIRouter()

API_STARTED_AT = int(time.time())

@router.get("")
async def health() -> dict[str, Any]:
    now = int(time.time())

    bot: dict[str, Any]
    try:
        r = get_redis()
        started_at_raw = await r.get(BOT_STARTED_AT_KEY)
        heartbeat_raw = await r.get(BOT_HEARTBEAT_KEY)

        started_at = int(started_at_raw) if started_at_raw else None
        heartbeat = int(heartbeat_raw) if heartbeat_raw else None

        fresh = (
            heartbeat is not None
            and (now - heartbeat) <= HEARTBEAT_WINDOW_SECONDS
        )
        bot = {
            "status": "online" if fresh else "offline",
            # started_at has no TTL. Ignore it once the heartbeat is gone,
            # or a crashed bot's uptime climbs forever.
            "uptime_seconds": (now - started_at) if fresh and started_at else None,
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
        # Public product config, not liveness: the web app's logged-out
        # /delete-account and /privacy pages state the retention period with no
        # token and before any deletion exists, so they need an unauthenticated
        # source for it. Authenticated deletion payloads carry the window
        # actually applied to that account instead — see app/schemas/deletion.py.
        "grace_days": settings.account_deletion_grace_days,
    }
