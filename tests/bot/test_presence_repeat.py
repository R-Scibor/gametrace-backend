"""Repeated `none->game` presence events for an already-ONGOING game.

Discord sometimes redelivers a game-start presence update with an empty
`before.activities`, so `before_game` reads None even though the game is still
running. The handler must recognise the stale ONGOING is the *same* game and
leave it untouched, rather than ERRORing it and opening a fresh session (which
produced same-second same-game ERROR churn — see docs/internal/incidents.md).
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

import app.bot.main as bot_main

pytestmark = pytest.mark.asyncio


def _member(game_name, *, member_id=1):
    activity = SimpleNamespace(name=game_name, type=None)
    return SimpleNamespace(id=member_id, bot=False, activities=[activity] if game_name else [])


async def _run_presence(*, ongoing_game_id, resolved_game_id, error_session, start_session):
    """Fire a `none->ROBLOX` event with an existing ONGOING and mocked deps."""
    before = _member(None)
    after = _member("ROBLOX")

    ongoing = SimpleNamespace(id=99, game_id=ongoing_game_id)
    resolved_game = SimpleNamespace(id=resolved_game_id)

    with patch.object(bot_main, "_get_game_name", side_effect=[None, "ROBLOX"]), \
         patch.object(bot_main, "_queue_enrichment"), \
         patch.object(bot_main, "AsyncSessionLocal") as sess, \
         patch("app.bot.session_manager.get_user_if_tracked",
               new=AsyncMock(return_value=SimpleNamespace(discord_id="1"))), \
         patch("app.bot.session_manager.get_ongoing_session",
               new=AsyncMock(return_value=ongoing)), \
         patch("app.bot.session_manager.get_or_create_game",
               new=AsyncMock(return_value=(resolved_game, False))), \
         patch("app.bot.session_manager.error_session", new=error_session), \
         patch("app.bot.session_manager.start_or_resume_session", new=start_session):
        sess.return_value.__aenter__ = AsyncMock(return_value=AsyncMock())
        sess.return_value.__aexit__ = AsyncMock(return_value=False)
        await bot_main.on_presence_update(before, after)


async def test_repeat_start_same_game_preserves_ongoing():
    """Same game already ONGOING -> no ERROR, no new session."""
    error_session = AsyncMock()
    start_session = AsyncMock()

    await _run_presence(
        ongoing_game_id=7,
        resolved_game_id=7,
        error_session=error_session,
        start_session=start_session,
    )

    error_session.assert_not_called()
    start_session.assert_not_called()


async def test_start_different_game_errors_stale_ongoing():
    """A stale ONGOING for a *different* game is still errored, then restarted."""
    error_session = AsyncMock()
    start_session = AsyncMock()

    await _run_presence(
        ongoing_game_id=3,
        resolved_game_id=7,
        error_session=error_session,
        start_session=start_session,
    )

    error_session.assert_awaited_once()
    start_session.assert_awaited_once()
