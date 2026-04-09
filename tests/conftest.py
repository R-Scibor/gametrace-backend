import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base, get_db
from app.main import app
from tests.factories import make_token, make_user

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture
async def engine():
    _engine = create_async_engine(TEST_DB_URL, echo=False)
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield _engine
    await _engine.dispose()


@pytest.fixture
async def db(engine):
    _factory = async_sessionmaker(engine, expire_on_commit=False)
    async with _factory() as session:
        yield session


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
    """Default test user, pre-created in DB."""
    return await make_user(db)


@pytest.fixture
async def authed_client(db, client, user):
    """HTTP client with Bearer token for the default test user."""
    token = await make_token(db, user.discord_id)
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client
