"""Sessions start at Discord's activity start when it is usable."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import discord
from sqlalchemy import select

from app.bot.activity import playing_started_at
from app.bot.reconcile import reconcile_user
from app.bot.self_healing import run_self_healing
from app.bot.session_manager import start_session, usable_start
from app.models.session import GameSession, SessionSource, SessionStatus
from tests.factories import dt, make_game, make_session, make_user


def _close(a: datetime, b: datetime, seconds: float = 5) -> bool:
    return abs((a - b).total_seconds()) < seconds


def _patch_queue(monkeypatch) -> None:
    monkeypatch.setattr("app.bot.reconcile.queue_enrichment", lambda game_id: None)


async def _ongoing(db, user_id: str) -> GameSession:
    result = await db.execute(
        select(GameSession).where(
            GameSession.user_id == user_id,
            GameSession.status == SessionStatus.ONGOING,
        )
    )
    return result.scalar_one()


def _guild(discord_id: str, name: str, start: datetime | None) -> MagicMock:
    activity = discord.Activity(type=discord.ActivityType.playing, name=name)
    if start is not None:
        activity = discord.Activity(
            type=discord.ActivityType.playing,
            name=name,
            timestamps={"start": int(start.timestamp() * 1000)},
        )
    member = MagicMock(spec=discord.Member)
    member.id = int(discord_id)
    member.bot = False
    member.activities = [activity]
    guild = MagicMock(spec=discord.Guild)
    guild.members = [member]
    guild.get_member.side_effect = lambda uid: member if uid == int(discord_id) else None
    return guild


# usable_start


def test_usable_start_none_is_now():
    now = datetime.now(UTC)
    assert usable_start(None, now) == now


def test_usable_start_past_is_kept_without_age_limit():
    now = datetime.now(UTC)
    old = now - timedelta(hours=36)
    assert usable_start(old, now) == old


def test_usable_start_future_falls_back_to_now():
    now = datetime.now(UTC)
    assert usable_start(now + timedelta(minutes=5), now) == now


def test_usable_start_naive_is_utc():
    now = datetime.now(UTC)
    naive = (now - timedelta(hours=1)).replace(tzinfo=None)
    assert usable_start(naive, now) == naive.replace(tzinfo=UTC)


def test_playing_started_at_reads_the_activity_start():
    start = dt(hours_ago=2).replace(microsecond=0)
    member = _guild("1", "Hades", start).get_member(1)
    assert playing_started_at(member) == start


# start_session


async def test_start_session_uses_started_at(db):
    user = await make_user(db)
    game = await make_game(db)
    started = dt(hours_ago=3)

    session = await start_session(db, user.discord_id, game.id, started_at=started)

    assert session is not None
    assert session.start_time == started


async def test_start_session_overlap_falls_back_to_now(db):
    user = await make_user(db)
    game = await make_game(db, "Hades")
    other = await make_game(db, "Minecraft")
    await make_session(
        db,
        user.discord_id,
        game.id,
        start_time=dt(hours_ago=5),
        end_time=dt(hours_ago=1),
        status=SessionStatus.COMPLETED,
        source=SessionSource.BOT,
    )

    session = await start_session(db, user.discord_id, other.id, started_at=dt(hours_ago=3))

    assert session is not None
    assert _close(session.start_time, datetime.now(UTC))


# reconcile_user, live


async def test_live_first_sighting_uses_activity_start(db, monkeypatch):
    user = await make_user(db)
    _patch_queue(monkeypatch)
    started = dt(hours_ago=2)

    await reconcile_user(
        db, user.discord_id, before_name=None, after_name="Hades",
        gap=False, after_started_at=started,
    )

    assert (await _ongoing(db, user.discord_id)).start_time == started


async def test_live_switch_uses_now(db, monkeypatch):
    user = await make_user(db)
    game = await make_game(db, "Hades")
    await make_session(
        db, user.discord_id, game.id,
        start_time=dt(hours_ago=1),
        status=SessionStatus.ONGOING,
        source=SessionSource.BOT,
    )
    _patch_queue(monkeypatch)

    await reconcile_user(
        db, user.discord_id, before_name="Hades", after_name="Minecraft",
        gap=False, after_started_at=dt(hours_ago=2),
    )

    assert _close((await _ongoing(db, user.discord_id)).start_time, datetime.now(UTC))


# self-healing


async def test_self_healing_starts_a_missed_session_at_activity_start(db, monkeypatch):
    user = await make_user(db)
    _patch_queue(monkeypatch)
    started = dt(hours_ago=2).replace(microsecond=0)

    await run_self_healing(db, guilds=[_guild(user.discord_id, "Hades", started)])

    assert (await _ongoing(db, user.discord_id)).start_time == started


async def test_self_healing_ignores_unregistered_players(db, monkeypatch):
    _patch_queue(monkeypatch)

    await run_self_healing(db, guilds=[_guild("222222222222222222", "Hades", dt(hours_ago=1))])

    result = await db.execute(select(GameSession))
    assert result.scalars().all() == []


async def test_self_healing_switch_errors_old_and_new_uses_activity_start(db, monkeypatch):
    user = await make_user(db)
    game = await make_game(db, "Hades")
    old = await make_session(
        db, user.discord_id, game.id,
        start_time=dt(hours_ago=3),
        status=SessionStatus.ONGOING,
        source=SessionSource.BOT,
    )
    _patch_queue(monkeypatch)
    started = dt(hours_ago=1).replace(microsecond=0)

    await run_self_healing(db, guilds=[_guild(user.discord_id, "Minecraft", started)])

    await db.refresh(old)
    assert old.status == SessionStatus.ERROR
    assert (await _ongoing(db, user.discord_id)).start_time == started
