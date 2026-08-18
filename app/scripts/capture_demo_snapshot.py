"""One-time capture of a real user's play history into the demo seed tables.

Run once, by hand, against the live DB:

    docker compose run --rm api python -m app.scripts.capture_demo_snapshot <discord_id>
    docker compose run --rm api python -m app.scripts.capture_demo_snapshot <discord_id> --yes

Resolves the id to a user, prints the username and the row counts that
would be captured, and requires typing the username back to confirm before
truncating and writing anything — a mistyped id would otherwise silently
capture a different real user's history. `--yes` skips the prompt for
scripted runs.

`capture()` takes an AsyncSession so it's testable in isolation;
`capture_with_confirmation()` wraps it with the preview/confirm gate and is
also testable directly (its `confirm` callable is injectable). The
`__main__` block owns engine creation/disposal, following the pattern in
app/tasks/cleanup.py.

Exclusions (each has a distinct reason):
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

import argparse
import asyncio
import logging
import sys
from collections.abc import Callable

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.models.demo_seed import DemoSeedPreference, DemoSeedSession
from app.models.game import UserGamePreference
from app.models.session import GameSession, SessionStatus
from app.models.user import User

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


async def _preview(db: AsyncSession, source_discord_id: str) -> tuple[str, int, int] | None:
    """Resolve `source_discord_id` to a user and count what capture() would
    capture, without truncating or writing anything. Returns None when no
    such user exists."""
    user = await db.get(User, source_discord_id)
    if user is None:
        return None

    session_count = (
        await db.execute(
            select(func.count()).select_from(GameSession).where(
                GameSession.user_id == source_discord_id,
                GameSession.deleted_at.is_(None),
                GameSession.is_flicker.is_(False),
                GameSession.status == SessionStatus.COMPLETED,
            )
        )
    ).scalar_one()
    pref_count = (
        await db.execute(
            select(func.count())
            .select_from(UserGamePreference)
            .where(UserGamePreference.user_id == source_discord_id)
        )
    ).scalar_one()
    return user.username, session_count, pref_count


async def capture_with_confirmation(
    db: AsyncSession,
    source_discord_id: str,
    *,
    assume_yes: bool = False,
    confirm: Callable[[str], str] = input,
) -> tuple[int, int] | None:
    """Preview + gate + capture against `db`. Returns None, and captures
    nothing, when the id matches no user or the typed confirmation doesn't
    match the resolved username. Otherwise delegates to capture() and
    returns its result.

    A mistyped discord_id would otherwise silently re-snapshot a different
    real user's timestamped play history behind a public credential, with
    no error — just smaller row counts. `confirm` is injectable so this is
    testable without blocking on real stdin.
    """
    preview = await _preview(db, source_discord_id)
    if preview is None:
        print(f"No user found with discord_id={source_discord_id!r}. Nothing captured.")
        return None

    username, session_count, pref_count = preview
    print(f"Resolved discord_id={source_discord_id!r} -> username={username!r}")
    print(f"  sessions that would be captured:    {session_count}")
    print(f"  preferences that would be captured: {pref_count}")
    print(
        "This TRUNCATES demo_seed_sessions and demo_seed_preferences and "
        "replaces them with the above."
    )

    if not assume_yes:
        answer = confirm(f"Type the username ({username!r}) to confirm: ")
        if answer != username:
            print("Confirmation did not match. Nothing captured.")
            return None

    return await capture(db, source_discord_id)


async def _run_with_engine(source_discord_id: str, *, assume_yes: bool) -> tuple[int, int] | None:
    engine = create_async_engine(settings.database_url, echo=False)
    SessionLocal = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with SessionLocal() as db:
            return await capture_with_confirmation(db, source_discord_id, assume_yes=assume_yes)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="One-time capture of a real user's play history into the demo seed tables."
    )
    parser.add_argument("source_discord_id", help="discord_id of the user to capture")
    parser.add_argument(
        "--yes",
        action="store_true",
        help="skip the typed confirmation prompt (for scripted/CI runs)",
    )
    args = parser.parse_args()
    result = asyncio.run(_run_with_engine(args.source_discord_id, assume_yes=args.yes))
    if result is None:
        sys.exit(1)
