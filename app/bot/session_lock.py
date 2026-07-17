"""Per-user locks for bot session transitions.

History: an earlier implementation used session-scoped ``pg_advisory_lock`` on the
caller's pooled AsyncSession. After ``commit()`` SQLAlchemy returns the connection
to the pool; the matching ``pg_advisory_unlock`` often ran on a *different*
backend and was a no-op, stranding the lock on an idle pooled connection. That
wedged presence handlers for days (see docs/internal/incidents.md, 2026-07-17).

Why not the common Postgres alternatives here:
- ``pg_advisory_xact_lock`` auto-releases on commit, but presence handlers commit
  mid-critical-section (complete → start, error → start), so the lock would drop
  too early.
- Pinning a dedicated engine connection for a session-scoped advisory lock works
  but holds an extra pool connection for every waiter, amplifying pool exhaustion
  under the exact pileup we hit.

The bot is a single process. An in-process ``asyncio.Lock`` per user serializes
presence + self-healing across commits without holding DB backends while waiting.
If we ever run multiple bot replicas, switch to a distributed lock (Redis or a
carefully pinned PG lock with a timeout).
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession

# user_id → Lock for the current event loop. Cleared if the running loop changes
# (pytest creates a fresh loop per test; production bot has one loop for life).
_user_locks: dict[str, asyncio.Lock] = {}
_locks_loop_id: int | None = None


def _lock_for(user_id: str) -> asyncio.Lock:
    global _locks_loop_id
    loop_id = id(asyncio.get_running_loop())
    if _locks_loop_id != loop_id:
        _user_locks.clear()
        _locks_loop_id = loop_id
    lock = _user_locks.get(user_id)
    if lock is None:
        lock = asyncio.Lock()
        _user_locks[user_id] = lock
    return lock


@asynccontextmanager
async def user_session_lock(db: AsyncSession, user_id: str):
    """Serialize session mutations for one user within this bot process.

    ``db`` is accepted for call-site compatibility; locking is in-process.
    """
    _ = db
    async with _lock_for(user_id):
        yield
