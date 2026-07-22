"""Bot /stats command — populated, empty, unregistered, and error-path copy."""
from unittest.mock import AsyncMock, patch

from app.bot.commands import stats_command
from app.core.config import settings
from app.models.user import User
from tests.factories import make_user

_DISCORD_ID = "123456789012345678"
_USERNAME = "testdiscord"


@patch("app.bot.commands.api_client.get_review_count", new_callable=AsyncMock)
@patch("app.bot.commands.api_client.get_summary", new_callable=AsyncMock)
async def test_stats_lists_total_time_and_top_games(mock_summary, mock_review, db, monkeypatch):
    monkeypatch.setattr(settings, "gametrace_web_url", "https://gametrace.example")
    await make_user(db, discord_id=_DISCORD_ID, username=_USERNAME)
    mock_summary.return_value = {
        "total_seconds": 7200,
        "per_game": [
            {"game_id": 1, "game_name": "Hades", "total_seconds": 3600},
            {"game_id": 2, "game_name": "Celeste", "total_seconds": 1800},
            {"game_id": 3, "game_name": "Balatro", "total_seconds": 1200},
            {"game_id": 4, "game_name": "Overlooked Game", "total_seconds": 600},
        ],
        "pending_errors": [],
    }
    mock_review.return_value = 0

    msg = await stats_command(db, _DISCORD_ID)

    assert "ostatnie 7 dni" in msg.lower()
    assert "Hades" in msg
    assert "Celeste" in msg
    assert "Balatro" in msg
    assert "Overlooked Game" not in msg  # only top 3
    assert "0h" not in msg


@patch("app.bot.commands.api_client.get_review_count", new_callable=AsyncMock)
@patch("app.bot.commands.api_client.get_summary", new_callable=AsyncMock)
async def test_stats_empty_window_returns_empty_state_copy(mock_summary, mock_review, db, monkeypatch):
    monkeypatch.setattr(settings, "gametrace_web_url", "https://gametrace.example")
    await make_user(db, discord_id=_DISCORD_ID, username=_USERNAME)
    mock_summary.return_value = {"total_seconds": 0, "per_game": [], "pending_errors": []}
    mock_review.return_value = 0

    msg = await stats_command(db, _DISCORD_ID)

    assert "0h" not in msg
    assert "brak danych" not in msg.lower()
    # promise-shaped: confirms bot is watching + invites them back
    assert "obserwuj" in msg.lower() or "śledz" in msg.lower()


@patch("app.bot.commands.api_client.get_review_count", new_callable=AsyncMock)
@patch("app.bot.commands.api_client.get_summary", new_callable=AsyncMock)
async def test_stats_empty_window_with_review_count_keeps_review_line(
    mock_summary, mock_review, db, monkeypatch
):
    monkeypatch.setattr(settings, "gametrace_web_url", "https://gametrace.example")
    await make_user(db, discord_id=_DISCORD_ID, username=_USERNAME)
    mock_summary.return_value = {"total_seconds": 0, "per_game": [], "pending_errors": []}
    mock_review.return_value = 3

    msg = await stats_command(db, _DISCORD_ID)

    assert "3" in msg
    assert "https://gametrace.example" in msg


@patch("app.bot.commands.api_client.get_review_count", new_callable=AsyncMock)
@patch("app.bot.commands.api_client.get_summary", new_callable=AsyncMock)
async def test_stats_empty_window_with_pending_errors_keeps_fix_it_line(
    mock_summary, mock_review, db, monkeypatch
):
    monkeypatch.setattr(settings, "gametrace_web_url", "https://gametrace.example")
    await make_user(db, discord_id=_DISCORD_ID, username=_USERNAME)
    mock_summary.return_value = {
        "total_seconds": 0,
        "per_game": [],
        "pending_errors": [{"id": 1, "game_id": 1, "game_name": "Hades", "start_time": "2026-07-01T00:00:00Z"}],
    }
    mock_review.return_value = 0

    msg = await stats_command(db, _DISCORD_ID)

    assert "1" in msg
    assert "https://gametrace.example" in msg


async def test_stats_unregistered_returns_register_prompt_and_creates_no_row(db):
    msg = await stats_command(db, "999999999999999999")

    assert "nie jesteś zarejestrowany" in msg.lower()
    assert await db.get(User, "999999999999999999") is None


@patch("app.bot.commands.api_client.get_review_count", new_callable=AsyncMock)
@patch("app.bot.commands.api_client.get_summary", new_callable=AsyncMock)
async def test_stats_pending_errors_adds_fix_it_line(mock_summary, mock_review, db, monkeypatch):
    monkeypatch.setattr(settings, "gametrace_web_url", "https://gametrace.example")
    await make_user(db, discord_id=_DISCORD_ID, username=_USERNAME)
    mock_summary.return_value = {
        "total_seconds": 3600,
        "per_game": [{"game_id": 1, "game_name": "Hades", "total_seconds": 3600}],
        "pending_errors": [{"id": 1, "game_id": 1, "game_name": "Hades", "start_time": "2026-07-01T00:00:00Z"}],
    }
    mock_review.return_value = 0

    msg = await stats_command(db, _DISCORD_ID)

    assert "https://gametrace.example" in msg
    assert "1" in msg


@patch("app.bot.commands.api_client.get_review_count", new_callable=AsyncMock)
@patch("app.bot.commands.api_client.get_summary", new_callable=AsyncMock)
async def test_stats_review_count_adds_verification_line(mock_summary, mock_review, db, monkeypatch):
    monkeypatch.setattr(settings, "gametrace_web_url", "https://gametrace.example")
    await make_user(db, discord_id=_DISCORD_ID, username=_USERNAME)
    mock_summary.return_value = {
        "total_seconds": 3600,
        "per_game": [{"game_id": 1, "game_name": "Hades", "total_seconds": 3600}],
        "pending_errors": [],
    }
    mock_review.return_value = 2

    msg = await stats_command(db, _DISCORD_ID)

    assert "2" in msg
    assert "https://gametrace.example" in msg


@patch("app.bot.commands.api_client.get_review_count", new_callable=AsyncMock)
@patch("app.bot.commands.api_client.get_summary", new_callable=AsyncMock)
async def test_stats_api_error_returns_friendly_polish_copy(mock_summary, mock_review, db):
    from app.bot.api_client import BotApiError

    await make_user(db, discord_id=_DISCORD_ID, username=_USERNAME)
    mock_summary.side_effect = BotApiError("boom")
    mock_review.return_value = 0

    msg = await stats_command(db, _DISCORD_ID)

    assert "nie udało się" in msg.lower()
