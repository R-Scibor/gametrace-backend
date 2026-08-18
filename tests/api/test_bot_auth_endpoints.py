"""
GET /stats/summary, GET /sessions, and GET /games must accept EITHER credential:
- Authorization: Bearer <token>  (existing web/mobile path, untouched)
- X-Bot-Service-Secret + X-Discord-Id  (bot-service path, Task 4)

Same route, resolved by either credential — not a parallel bot-only endpoint.
Neither credential supplied must still 401.
"""
from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import settings
from tests.factories import make_game, make_session, make_token, make_user

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


async def test_stats_summary_bearer_token_wins_over_spoofed_discord_id_header(
    client, db, bot_auth_enabled
):
    """A valid bearer token for user A must resolve to A even if an attacker
    also attaches X-Discord-Id for a different user B (plus a valid bot
    secret) — the Authorization header must always take priority and the
    X-Discord-Id header must never be consulted when it is present.
    """
    user_a = await make_user(db, discord_id="111111111111111111", username="usera")
    user_b = await make_user(db, discord_id="222222222222222222", username="userb")

    game = await make_game(db, primary_name="Game A")
    now = datetime.now(UTC)
    # Distinguishable data: only A has a completed session, so total_seconds
    # is nonzero for A and would be 0 for B if the header won instead.
    await make_session(
        db,
        user_id=user_a.discord_id,
        game_id=game.id,
        start_time=now - timedelta(hours=1),
        end_time=now,
    )

    token_a = await make_token(db, user_a.discord_id)

    resp = await client.get(
        "/api/v1/stats/summary",
        headers={
            "Authorization": f"Bearer {token_a}",
            "X-Bot-Service-Secret": BOT_SECRET,
            "X-Discord-Id": user_b.discord_id,
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["total_seconds"] == 3600


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


# ── GET /games ────────────────────────────────────────────────────────────────

async def test_games_list_accepts_bearer_token(authed_client):
    resp = await authed_client.get("/api/v1/games")
    assert resp.status_code == 200


async def test_games_list_accepts_bot_headers(client, db, bot_auth_enabled):
    user = await make_user(db, discord_id="333333333333333333", username="botcaller")
    resp = await client.get("/api/v1/games", headers=_bot_headers(user.discord_id))
    assert resp.status_code == 200


async def test_games_list_401_without_any_credential(client, bot_auth_enabled):
    resp = await client.get("/api/v1/games")
    assert resp.status_code == 401


# ── Bot headers must not leak into the OpenAPI security schemes ──────────────

def test_bot_auth_not_registered_as_security_scheme():
    from app.main import app

    schema = app.openapi()
    security_schemes = schema.get("components", {}).get("securitySchemes", {})
    for scheme in security_schemes.values():
        assert scheme.get("name", "").lower() != "x-bot-service-secret"
