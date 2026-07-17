import asyncio
from unittest.mock import MagicMock

import pytest
from sqlalchemy import text

from app.bot.session_lock import user_session_lock
from app.core.database import AsyncSessionLocal, engine

pytestmark = pytest.mark.asyncio


def _fake_db():
    """session_lock ignores db; tests that only care about the mutex use a stub."""
    return MagicMock()


async def test_user_session_lock_serializes_same_user():
    results: list[int] = []

    async def worker(tag: int) -> None:
        async with user_session_lock(_fake_db(), "user-a"):
            results.append(tag)
            await asyncio.sleep(0.05)
            results.append(tag)

    await asyncio.gather(worker(1), worker(2))

    assert results == [1, 1, 2, 2]


async def test_lock_allows_mid_critical_section_awaits():
    """Presence handlers commit/await inside the lock; sections must not interleave."""
    order: list[str] = []

    async def worker(name: str) -> None:
        async with user_session_lock(_fake_db(), "commit-user"):
            order.append(f"{name}:enter")
            await asyncio.sleep(0.05)
            order.append(f"{name}:exit")

    await asyncio.gather(worker("a"), worker("b"))

    assert order in (
        ["a:enter", "a:exit", "b:enter", "b:exit"],
        ["b:enter", "b:exit", "a:enter", "a:exit"],
    )


async def test_does_not_hold_postgres_advisory_locks():
    """Regression: the old path used pg_advisory_lock and could strand it.

    The asyncio.Lock path must not create advisory locks at all, so mid-lock
    commits cannot leave an idle backend holding a session-scoped key.
    """
    async with AsyncSessionLocal() as db:
        async with user_session_lock(db, "no-advisory-user"):
            await db.execute(text("SELECT 1"))
            await db.commit()
            await db.execute(text("SELECT 1"))
            await db.commit()

    async with engine.connect() as conn:
        remaining = (
            await conn.execute(
                text("SELECT count(*) FROM pg_locks WHERE locktype = 'advisory'")
            )
        ).scalar()
        await conn.rollback()
    assert remaining == 0


async def test_concurrent_users_do_not_block_each_other():
    """Different user_ids must not share a lock (no global mutex)."""
    entered = asyncio.Event()
    release = asyncio.Event()
    order: list[str] = []

    async def holder() -> None:
        async with user_session_lock(_fake_db(), "user-holder"):
            order.append("holder:in")
            entered.set()
            await release.wait()
            order.append("holder:out")

    async def other() -> None:
        await entered.wait()
        async with user_session_lock(_fake_db(), "user-other"):
            order.append("other:in")
            order.append("other:out")
            release.set()

    await asyncio.gather(holder(), other())
    assert order == ["holder:in", "other:in", "other:out", "holder:out"]
