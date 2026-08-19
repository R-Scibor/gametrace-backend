"""Tests for the public health payload.

`grace_days` rides along here because the web app states the account-deletion
retention period on its logged-out /delete-account and /privacy pages — before
any deletion exists and with no token to authenticate — so it needs an
unauthenticated source for the number.
"""
from app.core.config import settings

URL = "/api/v1/health"


async def test_health_is_reachable_without_a_token(client):
    resp = await client.get(URL)

    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_health_exposes_grace_days(client):
    resp = await client.get(URL)

    assert resp.status_code == 200
    assert resp.json()["grace_days"] == settings.account_deletion_grace_days


async def test_health_grace_days_follows_the_setting(client, monkeypatch):
    monkeypatch.setattr(settings, "account_deletion_grace_days", 14)

    resp = await client.get(URL)

    assert resp.status_code == 200
    assert resp.json()["grace_days"] == 14
