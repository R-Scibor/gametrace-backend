"""Bot slash command logic — register, login code, logout."""
import secrets
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import fakeredis.aioredis
import pytest
from sqlalchemy import select

from app.bot import replies
from app.bot.api_client import BotApiError, PendingDeletionError
from app.bot.commands import (
    issue_login_code,
    issue_login_code_facts,
    logout_user,
    logout_user_facts,
    recent_command,
    recent_facts,
    register_user,
    stats_command,
    stats_facts,
)
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
    assert "🔑 Weź kod" in msg
    assert "/login" in msg


async def test_register_first_time_includes_web_hint_when_configured(db, monkeypatch):
    monkeypatch.setattr(settings, "gametrace_web_url", "https://gametrace.example")

    msg = await register_user(db, _DISCORD_ID, _USERNAME)

    assert "https://gametrace.example" in msg
    assert "albo kodem z panelu (🔑 Weź kod)" in msg


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
    assert "🔑 Weź kod" in msg
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
    purge_at = datetime.now(UTC) + timedelta(days=3)
    await make_user(
        db,
        discord_id=_DISCORD_ID,
        username="oldname",
        deletion_requested_at=datetime.now(UTC),
        purge_at=purge_at,
    )

    msg = await register_user(db, _DISCORD_ID, "newname")

    assert "już zarejestrowany" not in msg.lower()
    assert "usunię" in msg.lower()  # scheduled-deletion copy mentions it


async def test_register_scheduled_for_deletion_still_syncs_username(db):
    purge_at = datetime.now(UTC) + timedelta(days=3)
    await make_user(
        db,
        discord_id=_DISCORD_ID,
        username="oldname",
        deletion_requested_at=datetime.now(UTC),
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


async def test_issue_login_code_facts_reports_created_and_the_spaced_code(db, r, monkeypatch):
    monkeypatch.setattr(secrets, "randbelow", lambda n: 493_072)

    facts = await issue_login_code_facts(db, r, _DISCORD_ID, _USERNAME)

    assert facts.unconfigured is False
    assert facts.created is True
    assert len(facts.code.replace(" ", "")) == 6
    assert facts.code[3] == " "
    assert facts.code == "493 072"


async def test_issue_login_code_facts_existing_user_is_not_created(db, r, monkeypatch):
    await make_user(db, discord_id=_DISCORD_ID, username=_USERNAME)
    monkeypatch.setattr(secrets, "randbelow", lambda n: 493_072)

    facts = await issue_login_code_facts(db, r, _DISCORD_ID, _USERNAME)

    assert facts.unconfigured is False
    assert facts.created is False
    assert facts.code == "493 072"


async def test_issue_login_code_facts_unconfigured_has_no_code(db, r, monkeypatch):
    monkeypatch.setattr(settings, "link_code_secret", "")

    facts = await issue_login_code_facts(db, r, _DISCORD_ID, _USERNAME)

    assert facts.unconfigured is True
    assert facts.code == ""


async def test_issue_login_code_still_returns_login_reply(db, r, monkeypatch):
    monkeypatch.setattr(secrets, "randbelow", lambda n: 493_072)

    text = await issue_login_code(db, r, _DISCORD_ID, _USERNAME)

    assert text.startswith("Witaj w GameTrace!")


async def test_logout_facts_return_the_count_and_slash_keeps_its_sentence(db, r):
    # facts and the slash wrapper each delete the rows they count, so the
    # two assertions need separate users or the second call would see zero.
    await make_user(db, discord_id=_DISCORD_ID, username=_USERNAME)
    await make_token(db, _DISCORD_ID)
    await make_token(db, _DISCORD_ID)
    other = "999999999999999991"
    await make_user(db, discord_id=other, username=_USERNAME)
    await make_token(db, other)
    await make_token(db, other)

    facts = await logout_user_facts(db, r, _DISCORD_ID)

    assert facts.unregistered is False
    assert facts.count == 2
    assert await logout_user(db, r, other) == (
        "Wylogowano. Unieważniono 2 tokenów logowania w aplikacji."
    )


async def test_logout_facts_missing_user_is_unregistered(db, r):
    facts = await logout_user_facts(db, r, "999999999999999999")

    assert facts.unregistered is True
    assert facts.count == 0
    assert await logout_user(db, r, "999999999999999999") == "Nie jesteś zarejestrowany."


async def test_stats_facts_unregistered(db):
    facts = await stats_facts(db, "999999999999999999")

    assert facts.slash_body == replies.NOT_REGISTERED
    assert facts.raw_failure == facts.slash_body
    assert facts.total_seconds == 0
    assert facts.per_game == []
    assert facts.pending_errors_count == 0
    assert facts.review_count == 0
    assert await stats_command(db, "999999999999999999") == facts.slash_body


@patch("app.bot.commands.api_client.get_review_count", new_callable=AsyncMock)
@patch("app.bot.commands.api_client.get_summary", new_callable=AsyncMock)
async def test_stats_facts_pending_deletion(mock_summary, mock_review, db):
    await make_user(db, discord_id=_DISCORD_ID, username=_USERNAME, tz="UTC")
    mock_summary.side_effect = PendingDeletionError(
        purge_at="2026-08-15T00:00:00+00:00", days_left=4
    )
    expected = replies.pending_deletion_reply(
        purge_at="2026-08-15T00:00:00+00:00", days_left=4, user_timezone="UTC"
    )

    facts = await stats_facts(db, _DISCORD_ID)

    assert facts.slash_body == expected
    assert facts.raw_failure == facts.slash_body
    assert facts.total_seconds == 0
    assert facts.per_game == []
    assert await stats_command(db, _DISCORD_ID) == facts.slash_body


@patch("app.bot.commands.api_client.get_review_count", new_callable=AsyncMock)
@patch("app.bot.commands.api_client.get_summary", new_callable=AsyncMock)
async def test_stats_facts_api_error(mock_summary, mock_review, db):
    await make_user(db, discord_id=_DISCORD_ID, username=_USERNAME)
    mock_summary.side_effect = BotApiError("boom")

    facts = await stats_facts(db, _DISCORD_ID)

    assert facts.slash_body == replies.STATS_FAILURE
    assert facts.raw_failure == facts.slash_body
    assert facts.review_count == 0
    assert await stats_command(db, _DISCORD_ID) == facts.slash_body


@patch("app.bot.commands.api_client.get_review_count", new_callable=AsyncMock)
@patch("app.bot.commands.api_client.get_summary", new_callable=AsyncMock)
async def test_stats_facts_success_matches_slash_payload(mock_summary, mock_review, db, monkeypatch):
    monkeypatch.setattr(settings, "gametrace_web_url", "https://gametrace.example")
    await make_user(db, discord_id=_DISCORD_ID, username=_USERNAME)
    per_game = [{"game_id": 1, "game_name": "Hades", "total_seconds": 3600}]
    mock_summary.return_value = {
        "total_seconds": 7200,
        "per_game": per_game,
        "pending_errors": [{"id": 1}],
    }
    mock_review.return_value = 2

    facts = await stats_facts(db, _DISCORD_ID)

    assert facts.raw_failure is None
    assert facts.total_seconds == 7200
    assert facts.per_game == per_game
    assert facts.pending_errors_count == 1
    assert facts.review_count == 2
    assert await stats_command(db, _DISCORD_ID) == facts.slash_body


async def test_recent_facts_unregistered(db):
    facts = await recent_facts(db, "999999999999999999")

    assert facts.slash_body == replies.NOT_REGISTERED
    assert facts.raw_failure == facts.slash_body
    assert facts.sessions == []
    assert facts.user_timezone is None
    assert await recent_command(db, "999999999999999999") == facts.slash_body


@patch("app.bot.commands.api_client.get_recent_sessions", new_callable=AsyncMock)
async def test_recent_facts_pending_deletion(mock_recent, db):
    await make_user(db, discord_id=_DISCORD_ID, username=_USERNAME, tz="UTC")
    mock_recent.side_effect = PendingDeletionError(
        purge_at="2026-08-15T00:00:00+00:00", days_left=4
    )
    expected = replies.pending_deletion_reply(
        purge_at="2026-08-15T00:00:00+00:00", days_left=4, user_timezone="UTC"
    )

    facts = await recent_facts(db, _DISCORD_ID)

    assert facts.slash_body == expected
    assert facts.raw_failure == facts.slash_body
    assert facts.sessions == []
    assert facts.user_timezone is None
    assert await recent_command(db, _DISCORD_ID) == facts.slash_body


@patch("app.bot.commands.api_client.get_recent_sessions", new_callable=AsyncMock)
async def test_recent_facts_api_error(mock_recent, db):
    await make_user(db, discord_id=_DISCORD_ID, username=_USERNAME)
    mock_recent.side_effect = BotApiError("boom")

    facts = await recent_facts(db, _DISCORD_ID)

    assert facts.slash_body == replies.RECENT_FAILURE
    assert facts.raw_failure == facts.slash_body
    assert facts.sessions == []
    assert await recent_command(db, _DISCORD_ID) == facts.slash_body


@patch("app.bot.commands.api_client.get_recent_sessions", new_callable=AsyncMock)
async def test_recent_facts_success_matches_slash_payload(mock_recent, db):
    await make_user(db, discord_id=_DISCORD_ID, username=_USERNAME, tz="Europe/Warsaw")
    sessions = [
        {
            "id": 1,
            "game": {"primary_name": "Hades"},
            "start_time": "2026-07-20T14:05:00Z",
            "duration_seconds": 3600,
            "status": "COMPLETED",
        }
    ]
    mock_recent.return_value = sessions

    facts = await recent_facts(db, _DISCORD_ID)

    assert facts.raw_failure is None
    assert facts.sessions == sessions
    assert facts.user_timezone == "Europe/Warsaw"
    assert await recent_command(db, _DISCORD_ID) == facts.slash_body


async def test_register_without_language_stores_en(db):
    await register_user(db, _DISCORD_ID, _USERNAME)

    user = await db.get(User, _DISCORD_ID)
    assert user.language == "en"


async def test_register_language_is_set_on_create_and_not_overwritten(db):
    await register_user(db, _DISCORD_ID, _USERNAME, language="pl")

    user = await db.get(User, _DISCORD_ID)
    assert user.language == "pl"

    await register_user(db, _DISCORD_ID, "renamed", language="en")

    user = await db.get(User, _DISCORD_ID)
    assert user.language == "pl"
    assert user.username == "renamed"