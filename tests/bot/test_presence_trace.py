"""A presence change binds a trace id that is still set when enrichment is enqueued."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import patch

import discord
import pytest
import structlog

import app.bot.main as bot_main
from tests.factories import make_user

pytestmark = pytest.mark.asyncio


def _member(game_name: str | None, *, member_id: int = 1):
    return SimpleNamespace(
        id=member_id,
        bot=False,
        activities=[discord.Game(name=game_name)] if game_name else [],
    )


async def test_presence_change_binds_trace_id(db):
    """A real game start binds a trace_id visible at enqueue time."""
    user = await make_user(db)
    seen: dict[str, str | None] = {}

    def fake_queue(_game_id: int) -> None:
        seen["trace_id"] = structlog.contextvars.get_contextvars().get("trace_id")

    @asynccontextmanager
    async def use_db():
        yield db

    before = _member(None, member_id=int(user.discord_id))
    after = _member("Celeste", member_id=int(user.discord_id))

    with patch.object(bot_main, "AsyncSessionLocal", use_db), \
         patch("app.bot.reconcile.queue_enrichment", side_effect=fake_queue):
        await bot_main.on_presence_update(before, after)

    assert seen["trace_id"] is not None
    assert structlog.contextvars.get_contextvars().get("trace_id") is None
