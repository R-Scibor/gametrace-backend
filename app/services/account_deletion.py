"""Schedule an account for deletion.

Stamps the grace-period columns, revokes every credential (bearer tokens,
push-notification devices, pending login-link code), and errors out any
ONGOING session — so product use stops immediately. A later sweep (separate
task) purges the row once `purge_at` passes.
"""
import logging
import math
from datetime import UTC, datetime, timedelta

import redis.exceptions
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.session_manager import error_session
from app.core.config import settings
from app.core.redis import get_redis
from app.models.account_deletion_event import (
    EVENT_CANCELLED,
    EVENT_REQUESTED,
    record_deletion_event,
)
from app.models.session import GameSession, SessionStatus
from app.models.user import User, UserAuthToken, UserDevice
from app.services import link_codes

logger = logging.getLogger(__name__)

_ONGOING_SESSION_NOTE = "Session terminated: account scheduled for deletion."


def days_left(purge_at: datetime) -> int:
    """Ceiling of the time remaining until purge_at, in whole days.

    Never reads 0 while the account still exists (e.g. 25h left -> 2, not 1).
    Single source of truth for this formula — shared by the API (login
    response, the pending-deletion 403, the deletion-status endpoint) and the
    Discord bot (/register, /stats, /recent pending-deletion copy). Lives
    here rather than in app/core because it's specifically the
    account-deletion grace-period calculation, and this module is already
    the shared home for schedule/cancel deletion logic that both the API and
    the bot import from.
    """
    now = datetime.now(UTC)
    delta = purge_at - now
    return max(1, math.ceil(delta.total_seconds() / 86400))


async def schedule_deletion(db: AsyncSession, user: User) -> User:
    """Idempotent — a second call for an already-scheduled account returns the
    existing `deletion_requested_at`/`purge_at` untouched, no re-work.

    Locks the row (`SELECT ... FOR UPDATE`) before the idempotency check so
    concurrent duplicate requests for the same account serialize instead of
    both passing the check and each inserting a `requested` audit row.
    """
    user = (
        await db.execute(select(User).where(User.discord_id == user.discord_id).with_for_update())
    ).scalar_one()
    if user.purge_at is not None:
        return user

    now = datetime.now(UTC)
    user.deletion_requested_at = now
    user.purge_at = now + timedelta(days=settings.account_deletion_grace_days)

    # Insert before token/device deletes and error_session (which commits
    # internally) so the first flush includes the audit row.
    record_deletion_event(db, user.discord_id, EVENT_REQUESTED, purge_at=user.purge_at)

    await db.execute(delete(UserAuthToken).where(UserAuthToken.user_id == user.discord_id))
    await db.execute(delete(UserDevice).where(UserDevice.user_id == user.discord_id))

    result = await db.execute(
        select(GameSession).where(
            GameSession.user_id == user.discord_id,
            GameSession.status == SessionStatus.ONGOING,
        )
    )
    for session in result.scalars().all():
        await error_session(db, session, _ONGOING_SESSION_NOTE)

    await db.commit()
    await db.refresh(user)

    # Best-effort: the deletion above is already committed, so a Redis outage
    # here must not roll it back — it just leaves a link code to expire on its
    # own TTL instead of being flushed immediately.
    try:
        r = get_redis()
        await link_codes.discard_pending_code(r, user.discord_id)
    except (redis.exceptions.RedisError, ConnectionError, OSError):
        logger.warning(
            "schedule_deletion.link_code_flush_failed",
            extra={"discord_id": user.discord_id},
            exc_info=True,
        )

    return user


async def cancel_deletion(db: AsyncSession, user: User) -> bool:
    """Reverses a scheduled deletion. Returns `False` if the account was not
    scheduled — the caller must not report success for cancelling nothing.

    Does NOT restore what schedule_deletion already destroyed: auth tokens and
    device registrations stay revoked, and any session errored out at request
    time stays ERROR. Only the grace-period columns are cleared.

    Locks the row before the check for the same reason as schedule_deletion:
    concurrent calls must serialize, not both pass and each write a
    `cancelled` audit row.
    """
    user = (
        await db.execute(select(User).where(User.discord_id == user.discord_id).with_for_update())
    ).scalar_one()
    if user.purge_at is None:
        return False

    record_deletion_event(db, user.discord_id, EVENT_CANCELLED)
    user.deletion_requested_at = None
    user.purge_at = None

    await db.commit()
    await db.refresh(user)

    return True

