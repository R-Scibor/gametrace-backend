"""
Tests for the pending-deletion guard: an account scheduled for deletion
(`purge_at` set) must be rejected by every authenticated route, except
logout — which must keep working so a user can sign out during the grace
period.
"""

import pytest

from app.core.config import settings
from tests.factories import dt, make_token, make_user

BOT_SECRET = "test-bot-secret"


@pytest.fixture
def bot_auth_enabled(monkeypatch):
    monkeypatch.setattr(settings, "bot_service_secret", BOT_SECRET)


async def test_scheduled_user_gets_403_on_authenticated_route(db, client):
    user = await make_user(
        db,
        discord_id="333333333333333333",
        username="scheduled",
        deletion_requested_at=dt(hours_ago=2),
        purge_at=dt(hours_from_now=25),  # 25h out -> ceil to 2 days
    )
    token = await make_token(db, user.discord_id)
    client.headers.update({"Authorization": f"Bearer {token}"})

    resp = await client.get("/api/v1/profile/me")

    assert resp.status_code == 403
    body = resp.json()
    assert "purge_at" in body["detail"]
    assert "days_left" in body["detail"]
    assert body["detail"]["days_left"] == 2


async def test_unscheduled_user_gets_200(authed_client):
    resp = await authed_client.get("/api/v1/profile/me")

    assert resp.status_code == 200


async def test_logout_still_works_while_scheduled(db, client):
    user = await make_user(
        db,
        discord_id="444444444444444444",
        username="scheduled-logout",
        deletion_requested_at=dt(hours_ago=1),
        purge_at=dt(hours_from_now=48),
    )
    token = await make_token(db, user.discord_id)
    client.headers.update({"Authorization": f"Bearer {token}"})

    resp = await client.post("/api/v1/auth/logout")

    assert resp.status_code == 204


async def test_bot_credential_path_403s_for_scheduled_user(db, client, bot_auth_enabled):
    await make_user(
        db,
        discord_id="555555555555555555",
        username="scheduled-bot",
        deletion_requested_at=dt(hours_ago=3),
        purge_at=dt(hours_from_now=10),  # under a day -> ceil to 1
    )

    resp = await client.get(
        "/api/v1/sessions",
        headers={
            "X-Bot-Service-Secret": BOT_SECRET,
            "X-Discord-Id": "555555555555555555",
        },
    )

    assert resp.status_code == 403
    body = resp.json()
    assert "purge_at" in body["detail"]
    assert body["detail"]["days_left"] == 1
