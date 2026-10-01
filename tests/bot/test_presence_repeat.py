"""Handler-level presence cases. The decision table lives in test_reconcile.py."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import patch

import discord
import pytest
from sqlalchemy import select

import app.bot.main as bot_main
from app.models.session import GameSession, SessionSource, SessionStatus
from tests.factories import dt, make_alias, make_game, make_session, make_user

pytestmark = pytest.mark.asyncio


def _member(discord_id: str, game_name: str | None):
    return SimpleNamespace(
        id=int(discord_id),
        bot=False,
        activities=[discord.Game(name=game_name)] if game_name else [],
    )


@asynccontextmanager
async def _use_db(db):
    yield db


async def _fire(db, discord_id: str, before: str | None, after: str | None) -> None:
    with patch.object(bot_main, "AsyncSessionLocal", lambda: _use_db(db)):
        await bot_main.on_presence_update(
            _member(discord_id, before),
            _member(discord_id, after),
        )


async def test_repeat_start_same_game_preserves_ongoing(db):
    user = await make_user(db)
    game = await make_game(db, "Hades")
    await make_alias(db, game.id, "ROBLOX")
    session = await make_session(
        db,
        user.discord_id,
        game.id,
        start_time=dt(hours_ago=1),
        status=SessionStatus.ONGOING,
        source=SessionSource.BOT,
    )

    await _fire(db, user.discord_id, None, "ROBLOX")

    await db.refresh(session)
    assert session.status == SessionStatus.ONGOING
    rows = (
        await db.execute(select(GameSession).where(GameSession.user_id == user.discord_id))
    ).scalars().all()
    assert len(list(rows)) == 1


async def test_start_different_game_errors_stale_ongoing(db):
    user = await make_user(db)
    game = await make_game(db, "Hades")
    await make_alias(db, game.id, "Hades.exe")
    session = await make_session(
        db,
        user.discord_id,
        game.id,
        start_time=dt(hours_ago=1),
        status=SessionStatus.ONGOING,
        source=SessionSource.BOT,
    )

    await _fire(db, user.discord_id, None, "ROBLOX")

    await db.refresh(session)
    assert session.status == SessionStatus.ERROR
    assert session.notes == "Presence: open session did not match activity 'ROBLOX'."
    assert session.end_time is None
