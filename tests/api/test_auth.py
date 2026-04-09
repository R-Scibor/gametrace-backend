from datetime import datetime, timedelta, timezone

import pytest

from app.models.user import UserAuthToken
from tests.factories import make_token, make_user


async def test_login_success(client, db):
    await make_user(db)

    resp = await client.post("/api/v1/auth/login", json={"username": "testuser"})

    assert resp.status_code == 200
    data = resp.json()
    assert "token" in data
    assert data["username"] == "testuser"
    assert data["discord_id"] == "111111111111111111"


async def test_login_updates_timezone(client, db):
    await make_user(db)

    resp = await client.post(
        "/api/v1/auth/login", json={"username": "testuser", "timezone": "Europe/Warsaw"}
    )

    assert resp.status_code == 200
    assert resp.json()["timezone"] == "Europe/Warsaw"


async def test_login_unknown_user_returns_404(client):
    resp = await client.post("/api/v1/auth/login", json={"username": "nobody"})

    assert resp.status_code == 404
    assert "Discord" in resp.json()["detail"]


async def test_login_utc_timezone_not_stored(client, db):
    """Default UTC timezone should not overwrite existing timezone."""
    await make_user(db, tz="Europe/Warsaw")

    resp = await client.post(
        "/api/v1/auth/login", json={"username": "testuser", "timezone": "UTC"}
    )

    assert resp.status_code == 200
    assert resp.json()["timezone"] == "Europe/Warsaw"


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
    expired = UserAuthToken(
        user_id=user.discord_id,
        token=UserAuthToken.generate_token(),
        expires_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    db.add(expired)
    await db.flush()

    resp = await client.get(
        "/api/v1/stats/summary", headers={"Authorization": f"Bearer {expired.token}"}
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
    result = await db.execute(select(UAT).where(UAT.token == token_value))
    token_row = result.scalar_one()
    # expires_at should be roughly 30 days from now (not less)
    assert token_row.expires_at > datetime.now(timezone.utc) + timedelta(days=29)
