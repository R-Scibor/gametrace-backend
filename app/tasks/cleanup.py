"""Hard delete sweeper — nightly GC.

Two deletes in one transaction:
1. Soft-deleted game sessions older than 7 days go for real.
2. FCM tokens idle for 6+ months are purged so the weekly fan-out
   doesn't waste send attempts on abandoned devices.

`daily_user_stats` has no FK to `game_sessions`, so the aggregate
rollups produced by the Downsampling Engine aren't affected.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.celery_app import celery_app
from app.core.config import settings
from app.models.session import GameSession
from app.models.user import UserDevice

logger = logging.getLogger(__name__)

SESSION_GRACE_DAYS = 7
DEVICE_STALE_DAYS = 30 * 6


async def _run_cleanup(db: AsyncSession) -> tuple[int, int]:
    now = datetime.now(timezone.utc)
    session_cutoff = now - timedelta(days=SESSION_GRACE_DAYS)
    device_cutoff = now - timedelta(days=DEVICE_STALE_DAYS)

    sessions_deleted = (
        await db.execute(
            delete(GameSession).where(
                GameSession.deleted_at.is_not(None),
                GameSession.deleted_at < session_cutoff,
            )
        )
    ).rowcount or 0

    devices_deleted = (
        await db.execute(
            delete(UserDevice).where(UserDevice.last_active < device_cutoff)
        )
    ).rowcount or 0

    await db.commit()
    logger.info(
        "hard_delete_sweep: sessions=%d devices=%d",
        sessions_deleted,
        devices_deleted,
    )
    return sessions_deleted, devices_deleted


async def _run_with_engine() -> tuple[int, int]:
    engine = create_async_engine(settings.database_url, echo=False)
    SessionLocal = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )
    try:
        async with SessionLocal() as db:
            return await _run_cleanup(db)
    finally:
        await engine.dispose()


@celery_app.task(name="tasks.hard_delete_sweep")
def hard_delete_sweep() -> tuple[int, int]:
    return asyncio.run(_run_with_engine())
