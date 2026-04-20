"""Weekly report — one push per user per ISO week.

Phase 4 simplification: the Beat trigger fires once a week in UTC. All users
receive the push at that UTC moment regardless of their own timezone. Phase 5
will upgrade to hourly fan-out that respects users.timezone.

Idempotency: Redis key `weekly_report:{isoyear}-W{week}:{user_id}` (SET NX EX)
prevents duplicate sends if the beat scheduler double-fires or the task is
manually re-invoked.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import redis as redis_sync
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.celery_app import celery_app
from app.core.config import settings
from app.models.user import User
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

    Uses a single session for the whole run — send_to_user commits per user,
    so a failure on one user doesn't roll back the others' bookkeeping.
    """
    r = redis_sync.from_url(settings.redis_url, decode_responses=True)
    now = datetime.now(timezone.utc)
    sent = 0

    users = (
        await db.execute(
            select(User).where(
                User.weekly_report_enabled == True,  # noqa: E712
                User.push_enabled == True,  # noqa: E712
            )
        )
    ).scalars().all()

    for user in users:
        dedup = _dedup_key(user.discord_id, now)
        if not r.set(dedup, "1", nx=True, ex=DEDUP_KEY_TTL_SECONDS):
            logger.info("weekly_report: skip %s — dedup", user.discord_id)
            continue
        try:
            summary = await summary_for_user(db, user, days=7)
            title, body = _format_payload(summary)
            sent += await send_to_user(
                db,
                user.discord_id,
                title,
                body,
                data={"type": "weekly_report"},
            )
        except Exception:
            logger.exception("weekly_report: send failed for %s", user.discord_id)
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
