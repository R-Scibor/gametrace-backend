"""
tests/bot/test_self_healing.py

Phase 2 — integration tests for run_self_healing(db, guilds).
Uses real test DB + mocked Discord guilds/members.
"""
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import MagicMock

import discord
from sqlalchemy import select, update

from app.bot.self_healing import run_self_healing
from app.models.game import Game
from app.models.session import GameSession, SessionSource, SessionStatus
from tests.factories import dt, make_alias, make_game, make_session, make_user

# ── Guild / member helpers ────────────────────────────────────────────────────

def _guild(discord_id: str, game_name: str | None) -> MagicMock:
    """Mock guild where discord_id is playing game_name (or nothing)."""
    member = MagicMock(spec=discord.Member)
    member.activities = [discord.Game(name=game_name)] if game_name else []

    guild = MagicMock(spec=discord.Guild)
    guild.get_member.side_effect = lambda uid: member if uid == int(discord_id) else None
    return guild


def _absent_guild() -> MagicMock:
    """Mock guild where no member is found."""
    guild = MagicMock(spec=discord.Guild)
    guild.get_member.return_value = None
    return guild


# ── Tests ─────────────────────────────────────────────────────────────────────

async def test_no_ongoing_sessions_is_noop(db):
    user = await make_user(db)
    game = await make_game(db)
    await make_session(db, user.discord_id, game.id, dt(hours_ago=3), dt(hours_ago=2))

    await run_self_healing(db, guilds=[])  # no guilds, no ongoing — nothing to do


async def test_member_not_found_errors_session(db):
    user = await make_user(db)
    game = await make_game(db, "Hades")
    session = await make_session(
        db, user.discord_id, game.id,
        start_time=dt(hours_ago=1),
        status=SessionStatus.ONGOING,
        source=SessionSource.BOT,
    )

    await run_self_healing(db, guilds=[_absent_guild()])

    await db.refresh(session)
    assert session.status == SessionStatus.ERROR
    assert "not found" in session.notes


async def test_same_game_keeps_ongoing(db):
    user = await make_user(db)
    game = await make_game(db, "Hades")
    await make_alias(db, game.id, "Hades.exe")
    await make_alias(db, game.id, "Hades Steam")
    session = await make_session(
        db, user.discord_id, game.id,
        start_time=dt(hours_ago=1),
        status=SessionStatus.ONGOING,
        source=SessionSource.BOT,
    )

    await run_self_healing(db, guilds=[_guild(user.discord_id, "Hades Steam")])

    await db.refresh(session)
    assert session.status == SessionStatus.ONGOING
    assert session.notes is None


async def test_same_game_over_12h_stays_ongoing(db):
    user = await make_user(db)
    game = await make_game(db, "Hades")
    await make_alias(db, game.id, "Hades.exe")
    session = await make_session(
        db, user.discord_id, game.id,
        start_time=dt(hours_ago=13),
        status=SessionStatus.ONGOING,
        source=SessionSource.BOT,
    )
    original_start = session.start_time

    await run_self_healing(db, guilds=[_guild(user.discord_id, "Hades.exe")])

    await db.refresh(session)
    assert session.status == SessionStatus.ONGOING
    assert session.start_time == original_start
    assert session.notes is None
    rows = (
        await db.execute(select(GameSession).where(GameSession.user_id == user.discord_id))
    ).scalars().all()
    assert len(list(rows)) == 1


async def test_startup_ignores_a_non_bot_ongoing(db):
    user = await make_user(db)
    game = await make_game(db, "Hades")
    session = await make_session(
        db, user.discord_id, game.id,
        start_time=dt(hours_ago=1),
        status=SessionStatus.ONGOING,
        source=SessionSource.MANUAL,
    )

    await run_self_healing(db, guilds=[_guild(user.discord_id, None)])

    await db.refresh(session)
    assert session.status == SessionStatus.ONGOING
    assert session.notes is None


async def test_same_game_under_12h_stays_ongoing(db):
    """A session under 12h whose alias matches stays ONGOING. Age is not what keeps it."""
    user = await make_user(db)
    game = await make_game(db, "Hades")
    await make_alias(db, game.id, "Hades.exe")
    session = await make_session(
        db, user.discord_id, game.id,
        start_time=dt(hours_ago=11),
        status=SessionStatus.ONGOING,
        source=SessionSource.BOT,
    )

    await run_self_healing(db, guilds=[_guild(user.discord_id, "Hades.exe")])

    await db.refresh(session)
    assert session.status == SessionStatus.ONGOING


async def test_different_game_errors_old_starts_new(db):
    user = await make_user(db)
    game = await make_game(db, "Hades")
    old_session = await make_session(
        db, user.discord_id, game.id,
        start_time=dt(hours_ago=1),
        status=SessionStatus.ONGOING,
        source=SessionSource.BOT,
    )

    await run_self_healing(db, guilds=[_guild(user.discord_id, "Minecraft")])

    await db.refresh(old_session)
    assert old_session.status == SessionStatus.ERROR

    result = await db.execute(
        select(GameSession).where(
            GameSession.user_id == user.discord_id,
            GameSession.status == SessionStatus.ONGOING,
        )
    )
    new_session = result.scalar_one()
    new_game = await db.get(Game, new_session.game_id)
    assert new_game.primary_name == "Minecraft"
    assert old_session.end_time is None
    assert old_session.notes == (
        "Self-Healing: bot restarted, player switched from 'Hades' to 'Minecraft'."
    )
    assert new_session.id != old_session.id


async def test_no_game_playing_errors_session(db):
    user = await make_user(db)
    game = await make_game(db, "Hades")
    session = await make_session(
        db, user.discord_id, game.id,
        start_time=dt(hours_ago=1),
        status=SessionStatus.ONGOING,
        source=SessionSource.BOT,
    )

    await run_self_healing(db, guilds=[_guild(user.discord_id, None)])

    await db.refresh(session)
    assert session.status == SessionStatus.ERROR
    assert "no longer in-game" in session.notes


async def test_multiple_sessions_each_reconciled(db):
    """3 ONGOING sessions for 3 users — each processed independently."""
    game = await make_game(db, "Hades")
    users = [
        await make_user(db, discord_id=f"11111111111111111{i}", username=f"user{i}")
        for i in range(3)
    ]
    sessions = [
        await make_session(
            db, u.discord_id, game.id,
            start_time=dt(hours_ago=1),
            status=SessionStatus.ONGOING,
            source=SessionSource.BOT,
        )
        for u in users
    ]

    guilds = [_guild(u.discord_id, None) for u in users]
    await run_self_healing(db, guilds=guilds)

    for s in sessions:
        await db.refresh(s)
        assert s.status == SessionStatus.ERROR


async def test_self_healing_skips_a_session_completed_inside_the_lock(db, monkeypatch):
    user = await make_user(db)
    game = await make_game(db, "Hades")
    session = await make_session(
        db,
        user.discord_id,
        game.id,
        start_time=dt(hours_ago=1),
        status=SessionStatus.ONGOING,
        source=SessionSource.BOT,
    )
    session_id = session.id
    user_id = user.discord_id

    @asynccontextmanager
    async def _complete_inside_lock(db_session, _user_id):
        await db_session.execute(
            update(GameSession)
            .where(GameSession.id == session_id)
            .values(
                status=SessionStatus.COMPLETED,
                end_time=datetime.now(UTC),
                duration_seconds=3600,
            ),
            execution_options={"synchronize_session": False},
        )
        await db_session.commit()
        yield

    monkeypatch.setattr("app.bot.self_healing.user_session_lock", _complete_inside_lock)

    await run_self_healing(db, guilds=[])

    await db.refresh(session)
    assert session.status == SessionStatus.COMPLETED
    assert session.notes is None
    rows = await db.execute(select(GameSession).where(GameSession.user_id == user_id))
    assert len(rows.scalars().all()) == 1
