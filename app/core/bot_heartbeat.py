"""Redis keys the Discord bot writes and /health reads.

The heartbeat TTL and the stale window are the same number so a dead bot
cannot keep growing uptime_seconds from a started_at key that has no TTL.
"""

BOT_STARTED_AT_KEY = "bot:started_at"
BOT_HEARTBEAT_KEY = "bot:heartbeat"
HEARTBEAT_WINDOW_SECONDS = 90
