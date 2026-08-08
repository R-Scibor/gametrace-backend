"""Hard delete sweeper — nightly GC.

Two deletes in one transaction:
1. Soft-deleted game sessions older than 7 days go for real.
2. FCM tokens idle for 6+ months are purged so the weekly fan-out
   doesn't waste send attempts on abandoned devices.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.celery_app import celery_app
from app.core.config import settings
from app.models.account_deletion_event import EVENT_PURGED, record_deletion_event
from app.models.session import GameSession, SessionStatus
from app.models.user import User, UserDevice

logger = logging.getLogger(__name__)

DEVICE_STALE_DAYS = 30 * 6


async def _run_cleanup(db: AsyncSession) -> tuple[int, int]:
    now = datetime.now(timezone.utc)
    session_cutoff = now - timedelta(days=settings.trash_retention_days)
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


async def _run_flicker_purge(db: AsyncSession) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(
        seconds=settings.session_flicker_gc_margin_seconds
    )
    deleted = (
        await db.execute(
            delete(GameSession).where(
                GameSession.is_flicker.is_(True),
                GameSession.status == SessionStatus.COMPLETED,
                GameSession.end_time < cutoff,
            )
        )
    ).rowcount or 0
    await db.commit()
    logger.info("purge_flicker_sessions: deleted=%d", deleted)
    return deleted


async def _run_flicker_with_engine() -> int:
    engine = create_async_engine(settings.database_url, echo=False)
    SessionLocal = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )
    try:
        async with SessionLocal() as db:
            return await _run_flicker_purge(db)
    finally:
        await engine.dispose()


@celery_app.task(name="tasks.purge_flicker_sessions")
def purge_flicker_sessions() -> int:
    return asyncio.run(_run_flicker_with_engine())


async def _run_purge(db: AsyncSession) -> int:
    """Permanently remove accounts whose grace period has expired.

    DELETE … RETURNING, then one account_deletion_events row per id
    (event=purged) in the same transaction. Catalog games are untouched.
    """
    now = datetime.now(timezone.utc)
    result = await db.execute(
        delete(User)
        .where(User.purge_at <= now)
        .returning(User.discord_id, User.purge_at)
    )
    rows = result.all()  # list of (discord_id, purge_at)
    for discord_id, purge_at in rows:
        record_deletion_event(db, discord_id, EVENT_PURGED, purge_at=purge_at)
    await db.commit()
    discord_ids = [discord_id for discord_id, _ in rows]
    logger.info(
        "account_deletion_purged",
        extra={"count": len(discord_ids), "discord_ids": discord_ids},
    )
    return len(discord_ids)


async def _run_purge_with_engine() -> int:
    engine = create_async_engine(settings.database_url, echo=False)
    SessionLocal = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )
    try:
        async with SessionLocal() as db:
            return await _run_purge(db)
    finally:
        await engine.dispose()


@celery_app.task(name="tasks.purge_deleted_accounts")
def purge_deleted_accounts() -> int:
    return asyncio.run(_run_purge_with_engine())
