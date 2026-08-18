"""Per-user quota for the paid voice endpoint (POST /voice/transcribe).

Keyed on ``user_id``, never on the bearer credential. Every login path issues a
NEW token, and nothing revokes the old ones, so a token-keyed counter was
resettable at will (log in again) and multipliable (hold N tokens for N budgets).
The endpoint resolves the User via get_current_user before this runs, so the
user id is available here — which is why the check lives in the handler rather
than in a slowapi key function (those see only the Request).

Two windows, two jobs:

* **Daily** bounds the bill. Counted from ``voice_usage``, which stores one row
  per PAID call (written as soon as Whisper returns, so a failed Gemini parse
  still counts) with ``user_id`` and ``created_at`` indexed — a cheap indexed
  count that survives a Redis flush. The insert is best-effort, so this count is
  a floor rather than an exact ledger; the hourly counter covers that gap.
* **Hourly** bounds burst, via the shared per-user counter in
  ``app.core.rate_limit``. It counts every ATTEMPT, so a loop of failing calls
  (which writes no ``voice_usage`` row) is still capped.

Daily is checked first and is read-only: a daily-blocked request spends no money
and so must not consume hourly budget.
"""
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rate_limit import check_hourly_quota, hourly_key
from app.models.voice_usage import VoiceUsage

logger = logging.getLogger(__name__)

HOURLY_LIMIT = 8
DAILY_LIMIT = 40
DAILY_WINDOW = timedelta(days=1)
BUCKET = "voice"


def voice_hourly_key(user_id: str) -> str:
    """The Redis key this quota's hourly window uses — exposed for tests."""
    return hourly_key(BUCKET, user_id)


async def check_voice_quota(db: AsyncSession, user_id: str) -> int | None:
    """Return Retry-After seconds when the user is over quota, else None.

    Consumes one unit of the hourly budget as a side effect when the daily cap
    still has room — call this once per request, immediately before the paid
    work.
    """
    now = datetime.now(UTC)

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

    # Fails open on a Redis outage (see check_hourly_quota) — voice stays usable
    # and the DB-backed daily cap above still binds, so spend stays bounded.
    return await check_hourly_quota(user_id, BUCKET, HOURLY_LIMIT)
