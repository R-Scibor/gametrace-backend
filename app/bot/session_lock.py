"""Per-user Postgres advisory locks for bot session transitions."""
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_LOCK_SQL = "SELECT pg_advisory_lock(abs(hashtext(:user_id)::bigint))"
_UNLOCK_SQL = "SELECT pg_advisory_unlock(abs(hashtext(:user_id)::bigint))"


@asynccontextmanager
async def user_session_lock(db: AsyncSession, user_id: str):
    """Serialize session mutations for one user across connections and commits."""
    await db.execute(text(_LOCK_SQL), {"user_id": user_id})
    try:
        yield
    finally:
        await db.execute(text(_UNLOCK_SQL), {"user_id": user_id})