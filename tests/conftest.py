"""
Test configuration.

Strategy:
- Tables are created once (module-level asyncio.run) before pytest's event loops start.
- Each test gets its own asyncpg connection via NullPool — no cross-loop sharing.
- join_transaction_mode="create_savepoint" turns every session.commit() into a
  SAVEPOINT, so the outer conn.rollback() undoes all changes after each test.
- Requires: CREATE DATABASE gametrace_test OWNER gametrace_user;  (run once)
"""
import asyncio

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings

# Never transmit test-run errors to the real Sentry project. Must run before
# `app.main` is imported, since that import calls init_sentry() at module load.
settings.sentry_dsn = ""

from app.core.database import Base, get_db  # noqa: E402  # must follow the sentry_dsn reset above
from app.main import app  # noqa: E402  # imports init_sentry() at module load
from tests.factories import make_token, make_user  # noqa: E402

TEST_DB_URL = settings.database_url.replace("/gametrace_db", "/gametrace_test")


# ── One-time DDL setup (runs before pytest's event loops are created) ─────────

async def _create_tables() -> None:
    engine = create_async_engine(TEST_DB_URL, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()


asyncio.run(_create_tables())


# ── Per-test fixtures ─────────────────────────────────────────────────────────

@pytest.fixture
async def db():
    """
    Yields an AsyncSession bound to a connection-level transaction.
    Everything is rolled back when the test ends — test DB stays clean.
    """
    engine = create_async_engine(TEST_DB_URL, poolclass=NullPool)
    async with engine.connect() as conn:
        await conn.begin()
        session = AsyncSession(
            bind=conn,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            yield session
        finally:
            await session.close()
            await conn.rollback()
    await engine.dispose()


@pytest.fixture
async def client(db):
    async def _override():
        yield db

    app.dependency_overrides[get_db] = _override
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture
async def user(db):
    """Default test user, pre-created in the test DB."""
    return await make_user(db)


@pytest.fixture
async def authed_client(db, client, user):
    """HTTP client with a valid Bearer token for the default test user."""
    token = await make_token(db, user.discord_id)
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


@pytest.fixture
async def admin_user(db):
    """Admin test user, pre-created in the test DB."""
    return await make_user(db, discord_id="222222222222222222", username="adminuser", is_admin=True)


@pytest.fixture
async def admin_client(db, client, admin_user):
    """HTTP client with a valid Bearer token for the admin test user."""
    token = await make_token(db, admin_user.discord_id)
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client
