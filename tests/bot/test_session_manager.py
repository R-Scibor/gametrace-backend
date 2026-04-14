"""
tests/bot/test_session_manager.py

Phase 2 — DB-layer functions used by the Discord bot.
Called directly with the test `db` fixture — no HTTP client, no Discord connection.
"""
from sqlalchemy import select

from app.bot.session_manager import (
    complete_session,
    error_session,
    get_ongoing_session,
    get_or_create_game,
    get_user_if_tracked,
    start_session,
)
from app.models.game import Game, GameAlias
from app.models.session import SessionSource, SessionStatus
from tests.factories import dt, make_alias, make_game, make_session, make_user


# ── get_user_if_tracked ───────────────────────────────────────────────────────

async def test_get_user_if_tracked_exists(db):
    user = await make_user(db)
    result = await get_user_if_tracked(db, user.discord_id)
    assert result is not None
    assert result.discord_id == user.discord_id


async def test_get_user_if_tracked_missing(db):
    result = await get_user_if_tracked(db, "nonexistent_id")
    assert result is None


# ── get_or_create_game ────────────────────────────────────────────────────────

async def test_get_or_create_game_new(db):
    game, created = await get_or_create_game(db, "Hades")

    assert created is True
    assert game.primary_name == "Hades"

    alias = await db.execute(
        select(GameAlias).where(GameAlias.discord_process_name == "Hades")
    )
    assert alias.scalar_one_or_none() is not None


async def test_get_or_create_game_existing(db):
    existing = await make_game(db, "Factorio")
    await make_alias(db, existing.id, "Factorio")

    game, created = await get_or_create_game(db, "Factorio")

    assert created is False
    assert game.id == existing.id


async def test_get_or_create_game_idempotent(db):
    await get_or_create_game(db, "Celeste")
    await get_or_create_game(db, "Celeste")

    result = await db.execute(select(Game).where(Game.primary_name == "Celeste"))
    assert len(result.scalars().all()) == 1


# ── start_session ─────────────────────────────────────────────────────────────

async def test_start_session_creates_ongoing(db):
    user = await make_user(db)
    game = await make_game(db)

    session = await start_session(db, user.discord_id, game.id)

    assert session.status == SessionStatus.ONGOING
    assert session.source == SessionSource.BOT
    assert session.end_time is None


# ── complete_session ──────────────────────────────────────────────────────────

async def test_complete_session(db):
    user = await make_user(db)
    game = await make_game(db)
    session = await make_session(
        db, user.discord_id, game.id,
        start_time=dt(hours_ago=1),
        status=SessionStatus.ONGOING,
        source=SessionSource.BOT,
    )

    completed = await complete_session(db, session)

    assert completed.status == SessionStatus.COMPLETED
    assert completed.end_time is not None
    assert 3500 <= completed.duration_seconds <= 3700


# ── error_session ─────────────────────────────────────────────────────────────

async def test_error_session(db):
    user = await make_user(db)
    game = await make_game(db)
    session = await make_session(
        db, user.discord_id, game.id,
        start_time=dt(hours_ago=1),
        status=SessionStatus.ONGOING,
        source=SessionSource.BOT,
    )

    errored = await error_session(db, session, "bot restarted")

    assert errored.status == SessionStatus.ERROR
    assert errored.notes == "bot restarted"


# ── get_ongoing_session ───────────────────────────────────────────────────────

async def test_get_ongoing_session_returns_it(db):
    user = await make_user(db)
    game = await make_game(db)
    session = await make_session(
        db, user.discord_id, game.id,
        start_time=dt(hours_ago=1),
        status=SessionStatus.ONGOING,
        source=SessionSource.BOT,
    )

    result = await get_ongoing_session(db, user.discord_id)
    assert result is not None
    assert result.id == session.id


async def test_get_ongoing_session_none_when_all_completed(db):
    user = await make_user(db)
    game = await make_game(db)
    await make_session(db, user.discord_id, game.id, dt(hours_ago=3), dt(hours_ago=2))

    result = await get_ongoing_session(db, user.discord_id)
    assert result is None
