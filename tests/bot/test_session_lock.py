import asyncio

import pytest

from app.bot.session_lock import user_session_lock
from app.core.database import AsyncSessionLocal

pytestmark = pytest.mark.asyncio


async def test_user_session_lock_serializes_cross_connection():
    results: list[int] = []

    async def worker(tag: int) -> None:
        async with AsyncSessionLocal() as db:
            async with user_session_lock(db, "user-a"):
                results.append(tag)
                await asyncio.sleep(0.1)
                results.append(tag)

    await asyncio.gather(worker(1), worker(2))

    assert results == [1, 1, 2, 2]