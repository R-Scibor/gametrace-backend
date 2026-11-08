"""Per-user interaction cooldowns and an in-process follow-up cap.

Redis errors fail open so a cooldown check never blocks the command. ``SET NX``
does not refresh an existing window. The follow-up counter is process-local and
only correct on a single event loop.
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)

MAX_IN_FLIGHT_FOLLOWUPS = 8

_WINDOWS_SECONDS = {
    "code": 30,
    "stats": 5,
    "recent": 5,
    "logout": 5,
}

_in_flight = 0


class Action(Enum):
    CODE = "code"
    STATS = "stats"
    RECENT = "recent"
    LOGOUT = "logout"


def _key(discord_id: str, action: Action) -> str:
    return f"bot:cd:{action.name.lower()}:{discord_id}"


def _window(action: Action) -> int:
    return _WINDOWS_SECONDS[action.name.lower()]


def _at_least_one(ttl: int) -> int:
    return 1 if ttl < 1 else ttl


async def cooldown_remaining(redis: Any, discord_id: str, action: Action) -> int | None:
    """Seconds left on the cooldown, or None when the key is absent or Redis fails."""
    try:
        ttl = await redis.ttl(_key(discord_id, action))
    except Exception:
        logger.warning("bot_interaction_guard.cooldown_read_failed", exc_info=True)
        return None
    # -2 is a missing key. -1 is present with no expiry; still report a wait.
    if ttl is None or ttl == -2:
        return None
    return _at_least_one(int(ttl))


async def claim_cooldown(redis: Any, discord_id: str, action: Action) -> int | None:
    """Arm the window. None means the caller won or Redis failed open.

    A loser gets the remaining seconds (at least 1). A missing TTL on that path
    returns the full window. NX keeps the original expiry.
    """
    window = _window(action)
    key = _key(discord_id, action)
    try:
        created = await redis.set(key, "1", nx=True, ex=window)
    except Exception:
        logger.warning("bot_interaction_guard.cooldown_set_failed", exc_info=True)
        return None
    if created:
        return None
    try:
        ttl = await redis.ttl(key)
    except Exception:
        logger.warning("bot_interaction_guard.cooldown_read_failed", exc_info=True)
        return None
    if ttl is None or ttl < 0:
        return window
    return _at_least_one(int(ttl))


def try_acquire_followup() -> bool:
    """Take one of the eight in-flight follow-up slots. False when the cap is full."""
    global _in_flight
    if _in_flight >= MAX_IN_FLIGHT_FOLLOWUPS:
        return False
    _in_flight += 1
    return True


def release_followup() -> None:
    """Return one slot. Raising on an empty counter stops a double release."""
    global _in_flight
    if _in_flight <= 0:
        raise RuntimeError("follow-up release without a claim")
    _in_flight -= 1


def reset_followups_for_tests() -> None:
    global _in_flight
    _in_flight = 0
