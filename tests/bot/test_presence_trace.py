from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import structlog

import app.bot.main as bot_main

pytestmark = pytest.mark.asyncio


def _member(game_name, *, bot=False, member_id=1):
    activity = SimpleNamespace(name=game_name, type=None)
    return SimpleNamespace(id=member_id, bot=bot, activities=[activity] if game_name else [])


async def test_presence_change_binds_trace_id():
    """A real game start binds a trace_id visible at enqueue time."""
    seen = {}

    def fake_queue(game_id):
        seen["trace_id"] = structlog.contextvars.get_contextvars().get("trace_id")

    before = _member(None)
    after = _member("Celeste")

    fake_game = SimpleNamespace(id=7)
    with patch.object(bot_main, "_get_game_name", side_effect=[None, "Celeste"]), \
         patch.object(bot_main, "_queue_enrichment", side_effect=fake_queue), \
         patch.object(bot_main, "AsyncSessionLocal") as sess, \
         patch("app.bot.session_manager.get_user_if_tracked",
               new=AsyncMock(return_value=SimpleNamespace(discord_id="1"))), \
         patch("app.bot.session_manager.get_ongoing_session",
               new=AsyncMock(return_value=None)), \
         patch("app.bot.session_manager.get_or_create_game",
               new=AsyncMock(return_value=(fake_game, True))), \
         patch("app.bot.session_manager.start_or_resume_session",
               new=AsyncMock(return_value=None)):
        sess.return_value.__aenter__ = AsyncMock(return_value=AsyncMock())
        sess.return_value.__aexit__ = AsyncMock(return_value=False)
        await bot_main.on_presence_update(before, after)

    assert seen["trace_id"] is not None
    assert structlog.contextvars.get_contextvars().get("trace_id") is None