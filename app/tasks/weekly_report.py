"""Weekly report — one push per user per ISO week.

Phase 4 simplification: the Beat trigger fires once a week in UTC. All users
receive the push at that UTC moment regardless of their own timezone. Phase 5
will upgrade to hourly fan-out that respects users.timezone.

Idempotency: Redis key `weekly_report:{isoyear}-W{week}:{user_id}` (SET NX EX)
prevents duplicate sends if the beat scheduler double-fires or the task is
manually re-invoked. The key is released when that user's send raises, or
when the user had devices and none accepted the push, so the next run can
retry. A user with no devices keeps the key.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

import redis as redis_sync
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.celery_app import celery_app
from app.core.config import settings
from app.models.user import User, UserDevice
from app.schemas.stats import StatsSummaryResponse
from app.services.fcm import send_to_user
from app.services.stats import summary_for_user

logger = logging.getLogger(__name__)

# Clear a day before the next Monday trigger so a recomputation can still
# dedupe within the same week if the scheduler fires twice.
DEDUP_KEY_TTL_SECONDS = 60 * 60 * 24 * 6


def _dedup_key(user_id: str, now: datetime) -> str:
    year, week, _ = now.isocalendar()
    return f"weekly_report:{year}-W{week:02d}:{user_id}"


def _release_dedup(r: redis_sync.Redis, key: str, user_id: str) -> None:
    try:
        r.delete(key)
    except redis_sync.RedisError:
        logger.exception("weekly_report: could not release dedup for %s", user_id)


def _format_payload(summary: StatsSummaryResponse) -> tuple[str, str]:
    hours = summary.total_seconds // 3600
    if summary.per_game:
        top = summary.per_game[0]
        body = f"Last week: {hours}h total. Top game: {top.game_name}."
    else:
        body = f"Last week: {hours}h across all games."
    return "GameTrace weekly report", body


async def _run_weekly_report(db: AsyncSession) -> int:
    """
    Fan-out over opted-in users. Returns total successful deliveries.

    Uses one session for the whole run. send_to_user commits a user it
    finished. A user that raises is rolled back before the next one, and
    their dedup key is released.
    """
    r = redis_sync.from_url(settings.redis_url, decode_responses=True)
    now = datetime.now(UTC)
    sent = 0

    # Ids only. rollback() expires every loaded instance, and the next user
    # cannot read attributes off that identity map.
    user_ids = (
        await db.execute(
            select(User.discord_id).where(
                User.weekly_report_enabled == True,  # noqa: E712
                User.push_enabled == True,  # noqa: E712
                User.purge_at.is_(None),
            )
        )
    ).scalars().all()

    for user_id in user_ids:
        dedup = _dedup_key(user_id, now)
        try:
            acquired = r.set(dedup, "1", nx=True, ex=DEDUP_KEY_TTL_SECONDS)
        except redis_sync.RedisError:
            logger.exception("weekly_report: dedup failed for %s", user_id)
            continue
        if not acquired:
            logger.info("weekly_report: skip %s — dedup", user_id)
            continue
        try:
            user = await db.get(User, user_id)
            if user is None:
                _release_dedup(r, dedup, user_id)
                continue
            summary = await summary_for_user(db, user, days=7)
            title, body = _format_payload(summary)
            # Count before send: a total failure deletes dead tokens, and the
            # key still has to be released so the next run can retry.
            device_count = await db.scalar(
                select(func.count())
                .select_from(UserDevice)
                .where(UserDevice.user_id == user_id)
            )
            delivered = await send_to_user(
                db,
                user_id,
                title,
                body,
                data={"type": "weekly_report"},
            )
            sent += delivered
            if delivered == 0 and device_count:
                _release_dedup(r, dedup, user_id)
        except Exception:
            logger.exception("weekly_report: send failed for %s", user_id)
            await db.rollback()
            _release_dedup(r, dedup, user_id)
    return sent


async def _run_with_engine() -> int:
    engine = create_async_engine(settings.database_url, echo=False)
    SessionLocal = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )
    try:
        async with SessionLocal() as db:
            return await _run_weekly_report(db)
    finally:
        await engine.dispose()


@celery_app.task(name="tasks.weekly_report")
def weekly_report() -> int:
    return asyncio.run(_run_with_engine())
