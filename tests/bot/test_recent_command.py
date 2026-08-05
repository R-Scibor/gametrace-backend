"""Bot /recent command — populated, empty, unregistered, error-path, ERROR-session copy."""
from unittest.mock import AsyncMock, patch

from app.bot.commands import recent_command
from app.core.config import settings
from app.models.user import User
from tests.factories import make_user

_DISCORD_ID = "123456789012345678"
_USERNAME = "testdiscord"


def _session(
    game_name="Hades",
    start_time="2026-07-20T14:05:00Z",
    duration_seconds=3600,
    status="COMPLETED",
):
    return {
        "id": 1,
        "game_id": 1,
        "game": {"id": 1, "primary_name": game_name, "cover_image_url": None},
        "start_time": start_time,
        "end_time": "2026-07-20T15:05:00Z" if duration_seconds is not None else None,
        "duration_seconds": duration_seconds,
        "status": status,
        "source": "BOT",
        "notes": None,
        "created_at": start_time,
        "deleted_at": None,
    }


@patch("app.bot.commands.api_client.get_recent_sessions", new_callable=AsyncMock)
async def test_recent_lists_sessions_with_game_time_and_duration(mock_recent, db, monkeypatch):
    monkeypatch.setattr(settings, "gametrace_web_url", "https://gametrace.example")
    await make_user(db, discord_id=_DISCORD_ID, username=_USERNAME, tz="Europe/Warsaw")
    mock_recent.return_value = [
        _session(game_name="Hades", duration_seconds=3600),
        _session(game_name="Celeste", duration_seconds=1800),
    ]

    msg = await recent_command(db, _DISCORD_ID)

    assert "Hades" in msg
    assert "Celeste" in msg
    assert "godz." in msg or "min" in msg
    # 14:05 UTC -> 16:05 Europe/Warsaw (CEST, UTC+2, in July)
    assert "16:05" in msg


@patch("app.bot.commands.api_client.get_recent_sessions", new_callable=AsyncMock)
async def test_recent_caps_at_five_sessions(mock_recent, db, monkeypatch):
    monkeypatch.setattr(settings, "gametrace_web_url", "https://gametrace.example")
    await make_user(db, discord_id=_DISCORD_ID, username=_USERNAME)
    mock_recent.return_value = [_session(game_name=f"Game{i}") for i in range(6)]

    msg = await recent_command(db, _DISCORD_ID)

    rendered = sum(1 for i in range(6) if f"Game{i}" in msg)
    assert rendered <= 5


@patch("app.bot.commands.api_client.get_recent_sessions", new_callable=AsyncMock)
async def test_recent_error_session_without_end_time_renders_without_duration(
    mock_recent, db, monkeypatch
):
    monkeypatch.setattr(settings, "gametrace_web_url", "https://gametrace.example")
    await make_user(db, discord_id=_DISCORD_ID, username=_USERNAME)
    mock_recent.return_value = [
        _session(
            game_name="Hades",
            start_time="2026-07-20T14:05:00Z",
            duration_seconds=None,
            status="ERROR",
        ),
    ]

    msg = await recent_command(db, _DISCORD_ID)

    assert "Hades" in msg
    assert "brak czasu trwania" in msg.lower()
    assert "-1" not in msg
    assert "godz." not in msg
    assert "min" not in msg.split("\n")[-1]  # no bogus duration on the session line


@patch("app.bot.commands.api_client.get_recent_sessions", new_callable=AsyncMock)
async def test_recent_excludes_ongoing_sessions(mock_recent, db, monkeypatch):
    monkeypatch.setattr(settings, "gametrace_web_url", "https://gametrace.example")
    await make_user(db, discord_id=_DISCORD_ID, username=_USERNAME)
    mock_recent.return_value = [
        _session(game_name="StillPlaying", duration_seconds=None, status="ONGOING"),
        _session(game_name="Hades", duration_seconds=3600),
    ]

    msg = await recent_command(db, _DISCORD_ID)

    assert "StillPlaying" not in msg
    assert "Hades" in msg


@patch("app.bot.commands.api_client.get_recent_sessions", new_callable=AsyncMock)
async def test_recent_empty_returns_empty_state_copy(mock_recent, db, monkeypatch):
    monkeypatch.setattr(settings, "gametrace_web_url", "https://gametrace.example")
    await make_user(db, discord_id=_DISCORD_ID, username=_USERNAME)
    mock_recent.return_value = []

    msg = await recent_command(db, _DISCORD_ID)

    assert "brak danych" not in msg.lower()
    # promise-shaped, matching /stats' empty-state bar
    assert "obserwuj" in msg.lower() or "pojawi" in msg.lower()
    # must not claim zero sessions were recorded — library_only filtering can
    # hide NEEDS_REVIEW/ERROR sessions that do exist, just aren't confirmed
    assert "żadnych zarejestrowanych sesji" not in msg.lower()


async def test_recent_unregistered_returns_register_prompt_and_creates_no_row(db):
    msg = await recent_command(db, "999999999999999999")

    assert "nie jesteś zarejestrowany" in msg.lower()
    assert await db.get(User, "999999999999999999") is None


@patch("app.bot.commands.api_client.get_recent_sessions", new_callable=AsyncMock)
async def test_recent_api_error_returns_friendly_polish_copy(mock_recent, db):
    from app.bot.api_client import BotApiError

    await make_user(db, discord_id=_DISCORD_ID, username=_USERNAME)
    mock_recent.side_effect = BotApiError("boom")

    msg = await recent_command(db, _DISCORD_ID)

    assert "nie udało się" in msg.lower()


@patch("app.bot.commands.api_client.get_recent_sessions", new_callable=AsyncMock)
async def test_recent_pending_deletion_is_not_reported_as_generic_failure(mock_recent, db):
    # Mechanism check: PendingDeletionError is a BotApiError subclass, so this
    # verifies recent_command routes it to the dedicated branch instead of
    # falling into the generic `except BotApiError` -> RECENT_FAILURE path.
    from app.bot.api_client import PendingDeletionError
    from app.bot import replies

    await make_user(db, discord_id=_DISCORD_ID, username=_USERNAME)
    mock_recent.side_effect = PendingDeletionError(
        purge_at="2026-08-15T00:00:00+00:00", days_left=4
    )

    msg = await recent_command(db, _DISCORD_ID)

    assert msg != replies.RECENT_FAILURE
    assert "nie udało się" not in msg.lower()
