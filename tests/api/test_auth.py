from datetime import datetime, timedelta, timezone

import pytest

from app.core.config import settings
from app.models.user import UserAuthToken
from tests.factories import dt, make_token, make_user

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


async def test_login_unscheduled_user_pending_deletion_is_null(client, db, dev_login_enabled):
    await make_user(db)

    resp = await client.post(
        "/api/v1/auth/login", json={"username": "testuser"}, headers=DEV_HEADERS
    )

    assert resp.status_code == 200
    assert resp.json()["pending_deletion"] is None


async def test_login_scheduled_user_returns_pending_deletion(client, db, dev_login_enabled):
    purge_at = dt(hours_from_now=25)  # 25h out -> ceil to 2 days
    await make_user(
        db,
        deletion_requested_at=dt(hours_ago=2),
        purge_at=purge_at,
    )

    resp = await client.post(
        "/api/v1/auth/login", json={"username": "testuser"}, headers=DEV_HEADERS
    )

    assert resp.status_code == 200
    pending = resp.json()["pending_deletion"]
    assert pending is not None
    assert pending["days_left"] == 2
    assert pending["purge_at"] is not None


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
    # HTTPBearer returns 403 when the Authorization header is missing entirely.
    # /stats/summary no longer demonstrates this: it also accepts a bot-service
    # credential (see test_bot_auth_endpoints.py), so a missing Authorization
    # header there now falls through to a 401. /stats/heatmap is untouched and
    # still uses plain get_current_user, so it still exhibits bare-HTTPBearer 403.
    resp = await client.get("/api/v1/stats/heatmap")

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
    """A request outside the debounce window slides last_active + expiry forward."""
    user = await make_user(db)
    raw = UserAuthToken.generate_token()
    stale = datetime.now(timezone.utc) - timedelta(hours=1)  # past the debounce window
    token = UserAuthToken(
        user_id=user.discord_id,
        token=UserAuthToken.hash_token(raw),
        last_active=stale,
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    db.add(token)
    await db.flush()

    resp = await client.get(
        "/api/v1/stats/summary", headers={"Authorization": f"Bearer {raw}"}
    )
    assert resp.status_code == 200

    await db.refresh(token)
    assert token.last_active > stale
    assert token.expires_at > datetime.now(timezone.utc) + timedelta(days=29)


async def test_token_activity_debounced_within_window(client, db, monkeypatch):
    """A request within the debounce window skips the last_active/expiry write."""
    monkeypatch.setattr(settings, "token_activity_debounce_minutes", 10)
    user = await make_user(db)
    raw = UserAuthToken.generate_token()
    recent = datetime.now(timezone.utc) - timedelta(minutes=2)  # inside the window
    expiry = datetime.now(timezone.utc) + timedelta(days=1)
    token = UserAuthToken(
        user_id=user.discord_id,
        token=UserAuthToken.hash_token(raw),
        last_active=recent,
        expires_at=expiry,
    )
    db.add(token)
    await db.flush()

    resp = await client.get(
        "/api/v1/stats/summary", headers={"Authorization": f"Bearer {raw}"}
    )
    assert resp.status_code == 200

    await db.refresh(token)
    assert token.last_active == recent
    assert token.expires_at == expiry
