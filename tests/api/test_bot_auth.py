"""
Unit tests for get_bot_user — the bot-service auth resolver.

This resolver is exercised directly (not through an HTTP route) because no
endpoint wires it yet: Tasks 2/4 wire it into GET /stats/summary and
GET /sessions alongside the code that consumes it. Testing the dependency
function in isolation lets us cover the auth matrix without picking an
endpoint to mutate here.
"""
import pytest
from fastapi import HTTPException

from app.api.v1.endpoints.auth import get_bot_user
from app.core.config import settings
from tests.factories import make_user

BOT_SECRET = "test-bot-secret"


@pytest.fixture
def bot_auth_enabled(monkeypatch):
    monkeypatch.setattr(settings, "bot_service_secret", BOT_SECRET)


async def test_valid_secret_and_known_id_returns_user(db, bot_auth_enabled):
    await make_user(db, discord_id="222222222222222222", username="botuser")

    user = await get_bot_user(
        x_bot_service_secret=BOT_SECRET,
        x_discord_id="222222222222222222",
        db=db,
    )

    assert user.discord_id == "222222222222222222"
    assert user.username == "botuser"


async def test_wrong_secret_returns_401(db, bot_auth_enabled):
    await make_user(db, discord_id="222222222222222222")

    with pytest.raises(HTTPException) as exc_info:
        await get_bot_user(
            x_bot_service_secret="wrong-secret",
            x_discord_id="222222222222222222",
            db=db,
        )

    assert exc_info.value.status_code == 401


async def test_missing_header_returns_401(db, bot_auth_enabled):
    await make_user(db, discord_id="222222222222222222")

    with pytest.raises(HTTPException) as exc_info:
        await get_bot_user(
            x_bot_service_secret=None,
            x_discord_id="222222222222222222",
            db=db,
        )

    assert exc_info.value.status_code == 401


async def test_setting_empty_returns_401_even_with_matching_header(db, monkeypatch):
    """Empty setting must fail closed — an absent/empty secret must never grant access."""
    monkeypatch.setattr(settings, "bot_service_secret", "")
    await make_user(db, discord_id="222222222222222222")

    with pytest.raises(HTTPException) as exc_info:
        await get_bot_user(
            x_bot_service_secret="",
            x_discord_id="222222222222222222",
            db=db,
        )

    assert exc_info.value.status_code == 401


async def test_valid_secret_unknown_discord_id_returns_404(db, bot_auth_enabled):
    with pytest.raises(HTTPException) as exc_info:
        await get_bot_user(
            x_bot_service_secret=BOT_SECRET,
            x_discord_id="999999999999999999",
            db=db,
        )

    assert exc_info.value.status_code == 404
