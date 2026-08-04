"""
tests/bot/test_deletion_gating.py

Task 6 — bot write-path gates for accounts scheduled for deletion.

Two independent write paths must stop writing for a `purge_at`-scheduled user:
  1. Presence writes via `get_user_if_tracked` (session_manager.py).
  2. Self-healing's switched-game branch (self_healing.py) — never calls
     `get_user_if_tracked`, so it needs its own gate.
"""
from datetime import timedelta, timezone
from unittest.mock import MagicMock

import discord
from sqlalchemy import select

from app.bot.self_healing import run_self_healing
from app.bot.session_manager import get_user_if_tracked
from app.models.session import GameSession, SessionStatus, SessionSource
from tests.factories import dt, make_game, make_session, make_user


def _guild(discord_id: str, game_name: str | None) -> MagicMock:
    """Mock guild where discord_id is playing game_name (or nothing)."""
    member = MagicMock(spec=discord.Member)
    member.activities = [discord.Game(name=game_name)] if game_name else []

    guild = MagicMock(spec=discord.Guild)
    guild.get_member.side_effect = lambda uid: member if uid == int(discord_id) else None
    return guild


# ── get_user_if_tracked ─────────────────────────────────────────────────────

async def test_get_user_if_tracked_returns_none_for_scheduled_user(db):
    user = await make_user(
        db,
        deletion_requested_at=dt(hours_ago=1),
        purge_at=dt(hours_from_now=24 * 7),
    )
    result = await get_user_if_tracked(db, user.discord_id)
    assert result is None


async def test_get_user_if_tracked_returns_user_for_normal_user(db):
    user = await make_user(db)
    result = await get_user_if_tracked(db, user.discord_id)
    assert result is not None
    assert result.discord_id == user.discord_id


# ── self-healing race case ──────────────────────────────────────────────────

async def test_self_healing_skips_new_session_for_scheduled_user(db):
    """
    A scheduled user has a leftover ONGOING session (e.g. presence event was
    already in flight when deletion was scheduled). On bot restart, self-healing
    sees them playing a DIFFERENT game. It must error the old session but must
    NOT start a new one for the account queued for erasure.
    """
    user = await make_user(
        db,
        deletion_requested_at=dt(hours_ago=1),
        purge_at=dt(hours_from_now=24 * 7),
    )
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
    assert result.scalar_one_or_none() is None
