"""Bot slash command logic — register, login code, logout."""
import secrets
from datetime import datetime, timedelta, timezone

import fakeredis.aioredis
import pytest
from sqlalchemy import select

from app.bot.commands import issue_login_code, logout_user, register_user
from app.core.config import settings
from app.models.user import User, UserAuthToken
from app.services import link_codes
from tests.factories import make_token, make_user

_SECRET = "test-link-code-secret"
_DISCORD_ID = "123456789012345678"
_USERNAME = "testdiscord"


@pytest.fixture
async def r():
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


@pytest.fixture(autouse=True)
def configured_secret(monkeypatch):
    monkeypatch.setattr(settings, "link_code_secret", _SECRET)


async def test_register_creates_user(db):
    msg = await register_user(db, _DISCORD_ID, _USERNAME)

    user = await db.get(User, _DISCORD_ID)
    assert user is not None
    assert user.username == _USERNAME
    assert msg.startswith("Zarejestrowano w GameTrace!")
    assert "nazwą Discord" not in msg


async def test_register_first_time_includes_orientation(db):
    msg = await register_user(db, _DISCORD_ID, _USERNAME)

    assert "Zarejestrowano w GameTrace!" in msg
    assert "/login" in msg


async def test_register_first_time_includes_web_hint_when_configured(db, monkeypatch):
    monkeypatch.setattr(settings, "gametrace_web_url", "https://gametrace.example")

    msg = await register_user(db, _DISCORD_ID, _USERNAME)

    assert "https://gametrace.example" in msg


async def test_register_creates_user_with_default_timezone(db):
    await register_user(db, _DISCORD_ID, _USERNAME)

    user = await db.get(User, _DISCORD_ID)
    assert user.timezone == settings.default_timezone


async def test_register_syncs_username(db):
    await make_user(db, discord_id=_DISCORD_ID, username="oldname")

    msg = await register_user(db, _DISCORD_ID, "newname")

    user = await db.get(User, _DISCORD_ID)
    assert user.username == "newname"
    assert msg.startswith("Jesteś już zarejestrowany.")
    assert "/login" in msg
    assert "nazwą Discord" not in msg


async def test_register_does_not_overwrite_existing_timezone(db):
    await make_user(db, discord_id=_DISCORD_ID, username="oldname", tz="America/New_York")

    await register_user(db, _DISCORD_ID, "newname")

    user = await db.get(User, _DISCORD_ID)
    assert user.timezone == "America/New_York"


async def test_register_scheduled_for_deletion_does_not_claim_already_registered(db):
    # Mechanism, not wording: an account with purge_at set must not take the
    # created=False "already registered" branch — that reply would be
    # actively misleading for an account queued for erasure.
    purge_at = datetime.now(timezone.utc) + timedelta(days=3)
    await make_user(
        db,
        discord_id=_DISCORD_ID,
        username="oldname",
        deletion_requested_at=datetime.now(timezone.utc),
        purge_at=purge_at,
    )

    msg = await register_user(db, _DISCORD_ID, "newname")

    assert "już zarejestrowany" not in msg.lower()
    assert "usunię" in msg.lower()  # scheduled-deletion copy mentions it


async def test_register_scheduled_for_deletion_still_syncs_username(db):
    purge_at = datetime.now(timezone.utc) + timedelta(days=3)
    await make_user(
        db,
        discord_id=_DISCORD_ID,
        username="oldname",
        deletion_requested_at=datetime.now(timezone.utc),
        purge_at=purge_at,
    )

    await register_user(db, _DISCORD_ID, "newname")

    user = await db.get(User, _DISCORD_ID)
    assert user.username == "newname"


async def test_issue_login_code_creates_user_and_redeemable_code(db, r, monkeypatch):
    monkeypatch.setattr(secrets, "randbelow", lambda n: 493_072)

    msg = await issue_login_code(db, r, _DISCORD_ID, _USERNAME)

    user = await db.get(User, _DISCORD_ID)
    assert user is not None
    assert user.username == _USERNAME
    assert "493 072" in msg
    assert "5 minut" in msg
    assert await link_codes.redeem_code(r, "493072") == _DISCORD_ID


async def test_issue_login_code_first_time_includes_onboarding_and_code(db, r, monkeypatch):
    monkeypatch.setattr(secrets, "randbelow", lambda n: 493_072)

    msg = await issue_login_code(db, r, _DISCORD_ID, _USERNAME)

    assert "Witaj w GameTrace!" in msg
    assert "493 072" in msg
    assert "5 minut" in msg


async def test_issue_login_code_returning_user_gets_terse_reply(db, r, monkeypatch):
    monkeypatch.setattr(settings, "gametrace_web_url", "")
    await make_user(db, discord_id=_DISCORD_ID, username=_USERNAME)
    monkeypatch.setattr(secrets, "randbelow", lambda n: 493_072)

    msg = await issue_login_code(db, r, _DISCORD_ID, _USERNAME)

    assert msg == "Twój kod logowania: **493 072**. Wpisz go w aplikacji w ciągu 5 minut."
    assert "Witaj w GameTrace!" not in msg


async def test_issue_login_code_returning_user_gets_link_when_web_url_configured(db, r, monkeypatch):
    monkeypatch.setattr(settings, "gametrace_web_url", "https://gametrace.example")
    await make_user(db, discord_id=_DISCORD_ID, username=_USERNAME)
    monkeypatch.setattr(secrets, "randbelow", lambda n: 493_072)

    msg = await issue_login_code(db, r, _DISCORD_ID, _USERNAME)

    assert msg == (
        "Twój kod logowania: **493 072**. Wpisz go w aplikacji lub na "
        "stronie w ciągu 5 minut: https://gametrace.example"
    )


async def test_issue_login_code_unconfigured_secret_returns_friendly_message(db, r, monkeypatch):
    monkeypatch.setattr(settings, "link_code_secret", "")

    msg = await issue_login_code(db, r, _DISCORD_ID, _USERNAME)

    assert "skonfigurowane" in msg.lower()
    assert await db.get(User, _DISCORD_ID) is not None


async def test_logout_revokes_tokens_and_pending_code(db, r, monkeypatch):
    await make_user(db, discord_id=_DISCORD_ID, username=_USERNAME)
    await make_token(db, _DISCORD_ID)
    await make_token(db, _DISCORD_ID)
    monkeypatch.setattr(secrets, "randbelow", lambda n: 111_111)
    await link_codes.issue_code(r, _DISCORD_ID)

    msg = await logout_user(db, r, _DISCORD_ID)

    assert "2" in msg
    result = await db.execute(
        select(UserAuthToken).where(UserAuthToken.user_id == _DISCORD_ID)
    )
    assert result.scalars().all() == []
    assert await link_codes.redeem_code(r, "111111") is None


async def test_logout_unregistered_returns_friendly_message(db, r):
    msg = await logout_user(db, r, "999999999999999999")

    assert "nie jesteś zarejestrowany" in msg.lower()