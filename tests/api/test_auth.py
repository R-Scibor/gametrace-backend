from datetime import datetime, timedelta, timezone

import pytest

from app.core.config import settings
from app.models.user import UserAuthToken
from tests.factories import make_token, make_user

DEV_SECRET = "test-dev-secret"
DEV_HEADERS = {"X-Dev-Login-Secret": DEV_SECRET}


@pytest.fixture
def dev_login_enabled(monkeypatch):
    """Configure the dev-login secret so /auth/login is enabled for the test."""
    monkeypatch.setattr(settings, "dev_login_secret", DEV_SECRET)


async def test_login_success(client, db, dev_login_enabled):
    await make_user(db)

    resp = await client.post(
        "/api/v1/auth/login", json={"username": "testuser"}, headers=DEV_HEADERS
    )

    assert resp.status_code == 200
    data = resp.json()
    assert "token" in data
    assert data["username"] == "testuser"
    assert data["discord_id"] == "111111111111111111"
    assert data["is_admin"] is False


async def test_login_persists_token_hashed(client, db, dev_login_enabled):
    """The raw token is returned to the client but only its SHA-256 is stored."""
    await make_user(db)

    resp = await client.post(
        "/api/v1/auth/login", json={"username": "testuser"}, headers=DEV_HEADERS
    )
    assert resp.status_code == 200
    raw = resp.json()["token"]

    from sqlalchemy import select

    row = (await db.execute(select(UserAuthToken))).scalar_one()
    assert row.token != raw
    assert row.token == UserAuthToken.hash_token(raw)


async def test_login_token_authenticates(client, db, dev_login_enabled):
    """A raw token from login authenticates a protected endpoint — create/lookup hashing agree."""
    await make_user(db)

    login = await client.post(
        "/api/v1/auth/login", json={"username": "testuser"}, headers=DEV_HEADERS
    )
    raw = login.json()["token"]

    resp = await client.get(
        "/api/v1/stats/summary", headers={"Authorization": f"Bearer {raw}"}
    )
    assert resp.status_code == 200


async def test_login_updates_timezone(client, db, dev_login_enabled):
    await make_user(db)

    resp = await client.post(
        "/api/v1/auth/login",
        json={"username": "testuser", "timezone": "Europe/Warsaw"},
        headers=DEV_HEADERS,
    )

    assert resp.status_code == 200
    assert resp.json()["timezone"] == "Europe/Warsaw"


async def test_login_unknown_user_returns_404(client, dev_login_enabled):
    resp = await client.post(
        "/api/v1/auth/login", json={"username": "nobody"}, headers=DEV_HEADERS
    )

    assert resp.status_code == 404
    assert "Discord" in resp.json()["detail"]


async def test_login_utc_timezone_not_stored(client, db, dev_login_enabled):
    """Default UTC timezone should not overwrite existing timezone."""
    await make_user(db, tz="Europe/Warsaw")

    resp = await client.post(
        "/api/v1/auth/login",
        json={"username": "testuser", "timezone": "UTC"},
        headers=DEV_HEADERS,
    )

    assert resp.status_code == 200
    assert resp.json()["timezone"] == "Europe/Warsaw"


async def test_login_disabled_when_secret_unset_returns_404(client, db, monkeypatch):
    """With no dev secret configured, name-only login must not exist."""
    monkeypatch.setattr(settings, "dev_login_secret", "")
    await make_user(db)

    resp = await client.post(
        "/api/v1/auth/login", json={"username": "testuser"}, headers=DEV_HEADERS
    )

    assert resp.status_code == 404


async def test_login_missing_secret_header_returns_404(client, db, dev_login_enabled):
    """Secret configured but no header presented — reject before touching the user."""
    await make_user(db)

    resp = await client.post("/api/v1/auth/login", json={"username": "testuser"})

    assert resp.status_code == 404


async def test_login_wrong_secret_returns_404(client, db, dev_login_enabled):
    await make_user(db)

    resp = await client.post(
        "/api/v1/auth/login",
        json={"username": "testuser"},
        headers={"X-Dev-Login-Secret": "wrong"},
    )

    assert resp.status_code == 404


async def test_logout_success(client, db):
    user = await make_user(db)
    token = await make_token(db, user.discord_id)

    resp = await client.post(
        "/api/v1/auth/logout", headers={"Authorization": f"Bearer {token}"}
    )

    assert resp.status_code == 204


async def test_logout_invalid_token_returns_401(client):
    resp = await client.post(
        "/api/v1/auth/logout", headers={"Authorization": "Bearer deadbeef"}
    )

    assert resp.status_code == 401


async def test_protected_endpoint_no_credentials_returns_403(client):
    # HTTPBearer returns 403 when the Authorization header is missing entirely
    resp = await client.get("/api/v1/stats/summary")

    assert resp.status_code == 403


async def test_protected_endpoint_bad_token_returns_401(client):
    resp = await client.get(
        "/api/v1/stats/summary", headers={"Authorization": "Bearer badtoken"}
    )

    assert resp.status_code == 401


async def test_protected_endpoint_expired_token_returns_401(client, db):
    user = await make_user(db)
    raw = UserAuthToken.generate_token()
    expired = UserAuthToken(
        user_id=user.discord_id,
        token=UserAuthToken.hash_token(raw),
        expires_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    db.add(expired)
    await db.flush()

    resp = await client.get(
        "/api/v1/stats/summary", headers={"Authorization": f"Bearer {raw}"}
    )

    assert resp.status_code == 401


async def test_token_expiry_extended_on_use(client, db):
    user = await make_user(db)
    token_value = await make_token(db, user.discord_id)

    from sqlalchemy import select
    from app.core.database import get_db  # noqa — just verifying DB state after request

    resp = await client.get(
        "/api/v1/stats/summary", headers={"Authorization": f"Bearer {token_value}"}
    )
    assert resp.status_code == 200

    from sqlalchemy import select
    from app.models.user import UserAuthToken as UAT
    result = await db.execute(select(UAT).where(UAT.token == UAT.hash_token(token_value)))
    token_row = result.scalar_one()
    # expires_at should be roughly 30 days from now (not less)
    assert token_row.expires_at > datetime.now(timezone.utc) + timedelta(days=29)
