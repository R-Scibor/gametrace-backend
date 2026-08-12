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
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.celery_app import celery_app
from app.core.config import settings
from app.models.account_deletion_event import (
    EVENT_CANCELLED,
    EVENT_PURGED,
    record_deletion_event,
)
from app.models.demo_seed import DemoSeedPreference, DemoSeedSession
from app.models.game import UserGamePreference
from app.models.report import Report
from app.models.session import GameSession, SessionStatus
from app.models.user import User, UserDevice
from app.models.voice_usage import VoiceUsage
from app.services.demo import DEMO_DISCORD_ID, DEMO_LANGUAGE, DEMO_TIMEZONE, DEMO_USERNAME

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
        # purge_at is set 7 days out, so this can't save the demo account from
        # a same-night deletion — that's not what it's for. It's a backstop
        # for the case where the nightly demo-reset task (which clears
        # purge_at on the demo account) has been silently failing for a week
        # or more and nobody noticed.
        .where(User.purge_at <= now, User.discord_id != DEMO_DISCORD_ID)
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


async def _run_demo_reset(db: AsyncSession) -> int:
    """Nightly restore of the demo reviewer account from its frozen snapshot.

    One transaction: delete the demo account's owned rows, restore from
    `demo_seed_*` with every timestamp shifted by a single delta, and upsert
    the user row to its canonical state — then commit all of it together.
    A failure partway (e.g. a snapshot `game_id` a merge later removed) must
    roll back everything rather than leave the demo account emptied for a
    credential nobody is watching overnight. `user_auth_tokens` are
    deliberately never touched here (30-day sliding sessions; the
    concurrent-token cap bounds them instead).
    """
    try:
        await db.execute(delete(GameSession).where(GameSession.user_id == DEMO_DISCORD_ID))
        await db.execute(
            delete(UserGamePreference).where(UserGamePreference.user_id == DEMO_DISCORD_ID)
        )
        await db.execute(delete(UserDevice).where(UserDevice.user_id == DEMO_DISCORD_ID))
        await db.execute(delete(VoiceUsage).where(VoiceUsage.user_id == DEMO_DISCORD_ID))
        await db.execute(delete(Report).where(Report.user_id == DEMO_DISCORD_ID))

        user = await db.get(User, DEMO_DISCORD_ID)
        if user is None:
            user = User(discord_id=DEMO_DISCORD_ID)
            db.add(user)
        user.username = DEMO_USERNAME
        user.timezone = DEMO_TIMEZONE
        user.language = DEMO_LANGUAGE
        user.is_admin = False
        user.weekly_report_enabled = True
        user.push_enabled = True
        # schedule_deletion writes EVENT_REQUESTED and cancel_deletion writes
        # EVENT_CANCELLED (app/services/account_deletion.py); clearing these
        # columns directly here bypasses both, which would leave an
        # unterminated `requested` row in account_deletion_events for the
        # demo account forever. Guard on purge_at actually being set so a
        # routine reset with nothing pending doesn't emit a spurious event.
        if user.purge_at is not None:
            record_deletion_event(db, DEMO_DISCORD_ID, EVENT_CANCELLED)
        user.deletion_requested_at = None
        user.purge_at = None

        seed_sessions = (await db.execute(select(DemoSeedSession))).scalars().all()

        delta = timedelta(0)
        if seed_sessions:
            latest_start = max(s.start_time for s in seed_sessions)
            demo_tz = ZoneInfo(DEMO_TIMEZONE)
            latest_date = latest_start.astimezone(demo_tz).date()
            today = datetime.now(demo_tz).date()
            delta = timedelta(days=(today - latest_date).days)

            # A whole-day delta preserves time-of-day, so a session that
            # started late in the evening (e.g. 22:00) can still land in the
            # future: the task runs at 03:00 UTC, well before 22:00 in any
            # timezone that day. A manual session created for that evening
            # would then 409 against a restored session that "hasn't
            # happened yet". Clamp by reducing the single shared delta a
            # whole day at a time — never rebase rows individually, since
            # preserving relative spacing is the entire point of one delta.
            now = datetime.now(timezone.utc)
            latest_moment = max(
                (s.end_time if s.end_time is not None else s.start_time)
                for s in seed_sessions
            )
            latest_moment = max(latest_moment, latest_start)
            while latest_moment + delta > now:
                delta -= timedelta(days=1)

        for seed in seed_sessions:
            db.add(
                GameSession(
                    user_id=DEMO_DISCORD_ID,
                    game_id=seed.game_id,
                    start_time=seed.start_time + delta,
                    end_time=(seed.end_time + delta) if seed.end_time is not None else None,
                    duration_seconds=seed.duration_seconds,
                    status=seed.status,
                    source=seed.source,
                )
            )

        seed_prefs = (await db.execute(select(DemoSeedPreference))).scalars().all()
        # Dedupe by game_id before restoring. demo_seed_preferences itself
        # carries no uniqueness constraint, but its restore destination
        # (user_game_preferences) has UNIQUE(user_id, game_id) — two seed
        # rows sharing a game_id (e.g. left behind by a game merge) would
        # violate it and roll back this entire transaction, and since the
        # duplicate lives in the frozen snapshot itself, every future reset
        # would fail identically. Last one wins; these are cosmetic fields.
        deduped_prefs = {seed_pref.game_id: seed_pref for seed_pref in seed_prefs}
        for seed_pref in deduped_prefs.values():
            db.add(
                UserGamePreference(
                    user_id=DEMO_DISCORD_ID,
                    game_id=seed_pref.game_id,
                    is_ignored=seed_pref.is_ignored,
                    is_accepted=seed_pref.is_accepted,
                    custom_tag=seed_pref.custom_tag,
                )
            )

        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("reset_demo_account: failed, rolled back")
        raise

    restored = len(seed_sessions)
    logger.info("reset_demo_account: restored=%d delta_days=%d", restored, delta.days)
    return restored


async def _run_demo_reset_with_engine() -> int:
    engine = create_async_engine(settings.database_url, echo=False)
    SessionLocal = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )
    try:
        async with SessionLocal() as db:
            return await _run_demo_reset(db)
    finally:
        await engine.dispose()


@celery_app.task(name="tasks.reset_demo_account")
def reset_demo_account() -> int:
    return asyncio.run(_run_demo_reset_with_engine())
