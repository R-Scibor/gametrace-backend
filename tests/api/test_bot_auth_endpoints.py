"""
GET /stats/summary and GET /sessions must accept EITHER credential:
- Authorization: Bearer <token>  (existing web/mobile path, untouched)
- X-Bot-Service-Secret + X-Discord-Id  (bot-service path, Task 4)

Same route, resolved by either credential — not a parallel bot-only endpoint.
Neither credential supplied must still 401.
"""
import pytest

from app.core.config import settings
from tests.factories import make_user

BOT_SECRET = "test-bot-secret"


@pytest.fixture
def bot_auth_enabled(monkeypatch):
    monkeypatch.setattr(settings, "bot_service_secret", BOT_SECRET)


def _bot_headers(discord_id: str) -> dict:
    return {"X-Bot-Service-Secret": BOT_SECRET, "X-Discord-Id": discord_id}


# ── GET /stats/summary ────────────────────────────────────────────────────────

async def test_stats_summary_accepts_bearer_token(authed_client):
    resp = await authed_client.get("/api/v1/stats/summary")
    assert resp.status_code == 200


async def test_stats_summary_accepts_bot_headers(client, db, bot_auth_enabled):
    user = await make_user(db, discord_id="333333333333333333", username="botcaller")
    resp = await client.get(
        "/api/v1/stats/summary", headers=_bot_headers(user.discord_id)
    )
    assert resp.status_code == 200


async def test_stats_summary_401_without_any_credential(client, bot_auth_enabled):
    resp = await client.get("/api/v1/stats/summary")
    assert resp.status_code == 401


# ── GET /sessions ─────────────────────────────────────────────────────────────

async def test_sessions_list_accepts_bearer_token(authed_client):
    resp = await authed_client.get("/api/v1/sessions")
    assert resp.status_code == 200


async def test_sessions_list_accepts_bot_headers(client, db, bot_auth_enabled):
    user = await make_user(db, discord_id="333333333333333333", username="botcaller")
    resp = await client.get("/api/v1/sessions", headers=_bot_headers(user.discord_id))
    assert resp.status_code == 200


async def test_sessions_list_401_without_any_credential(client, bot_auth_enabled):
    resp = await client.get("/api/v1/sessions")
    assert resp.status_code == 401


# ── Bot headers must not leak into the OpenAPI security schemes ──────────────

def test_bot_auth_not_registered_as_security_scheme():
    from app.main import app

    schema = app.openapi()
    security_schemes = schema.get("components", {}).get("securitySchemes", {})
    for scheme in security_schemes.values():
        assert scheme.get("name", "").lower() != "x-bot-service-secret"
