"""One-time capture of a real user's play history into the demo seed tables.

Run once, by hand, against the live DB:

    docker compose run --rm api python -m app.scripts.capture_demo_snapshot <discord_id>

`capture()` takes an AsyncSession so it's testable in isolation; the
`__main__` block below owns engine creation/disposal, following the pattern
in app/tasks/cleanup.py.

Exclusions (each has a distinct reason — see docs/internal spec):
- deleted_at IS NOT NULL: soft-deleted rows are trash, not library.
- is_flicker = true: the nightly flicker purge would delete restored rows
  again within a day (restore re-bases dates into the past), breaking
  restore row counts every night.
- Only status = COMPLETED is captured (a positive selection, not an
  exclusion list, so a future status defaults to excluded rather than
  silently admitted). Both ONGOING and ERROR rows have a NULL end_time,
  which would render as an unbounded-looking session after restore; ERROR
  rows additionally lose their explanatory text, since `notes` (where the
  self-healer records the error) is deliberately not copied.

`notes` is deliberately not copied — it's system-owned (self-healing writes
error text into it) and there is no destination column for it.
"""
from __future__ import annotations

import asyncio
import logging
import sys

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.models.demo_seed import DemoSeedPreference, DemoSeedSession
from app.models.game import UserGamePreference
from app.models.session import GameSession, SessionStatus

logger = logging.getLogger(__name__)


async def capture(db: AsyncSession, source_discord_id: str) -> tuple[int, int]:
    """Snapshot one user's library into the demo seed tables.

    Truncates both seed tables first so re-running replaces rather than
    duplicates. Returns (sessions_captured, preferences_captured).
    """
    await db.execute(delete(DemoSeedSession))
    await db.execute(delete(DemoSeedPreference))

    sessions = (
        await db.execute(
            select(GameSession).where(
                GameSession.user_id == source_discord_id,
                GameSession.deleted_at.is_(None),
                GameSession.is_flicker.is_(False),
                GameSession.status == SessionStatus.COMPLETED,
            )
        )
    ).scalars().all()

    for session in sessions:
        db.add(
            DemoSeedSession(
                game_id=session.game_id,
                start_time=session.start_time,
                end_time=session.end_time,
                duration_seconds=session.duration_seconds,
                status=session.status,
                source=session.source,
            )
        )

    preferences = (
        await db.execute(
            select(UserGamePreference).where(UserGamePreference.user_id == source_discord_id)
        )
    ).scalars().all()

    for pref in preferences:
        db.add(
            DemoSeedPreference(
                game_id=pref.game_id,
                is_ignored=pref.is_ignored,
                is_accepted=pref.is_accepted,
                custom_tag=pref.custom_tag,
            )
        )

    await db.commit()

    sessions_captured = len(sessions)
    preferences_captured = len(preferences)

    start_dates = [s.start_time for s in sessions if s.start_time is not None]
    date_range = (
        f"{min(start_dates)} .. {max(start_dates)}" if start_dates else "n/a (no sessions)"
    )

    print("Demo snapshot capture complete.")
    print(f"  sessions captured:     {sessions_captured}")
    print(f"  preferences captured:  {preferences_captured}")
    print(f"  session date range:    {date_range}")

    logger.info(
        "capture_demo_snapshot: sessions=%d preferences=%d",
        sessions_captured,
        preferences_captured,
    )

    return sessions_captured, preferences_captured


async def _run_with_engine(source_discord_id: str) -> tuple[int, int]:
    engine = create_async_engine(settings.database_url, echo=False)
    SessionLocal = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with SessionLocal() as db:
            return await capture(db, source_discord_id)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python -m app.scripts.capture_demo_snapshot <source_discord_id>")
        sys.exit(1)
    asyncio.run(_run_with_engine(sys.argv[1]))
