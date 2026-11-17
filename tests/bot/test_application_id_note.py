"""The application id is a note. It does not change which game a session uses."""

import logging
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import patch

import discord
import pytest
from sqlalchemy import select

import app.bot.main as bot_main
from app.bot.reconcile import _start_and_commit, reconcile_user
from app.bot.self_healing import run_self_healing
from app.bot.session_manager import resolve_alias
from app.models.game import EnrichmentStatus, GameAlias
from app.models.session import GameSession, SessionSource, SessionStatus
from tests.factories import dt, make_alias, make_game, make_session, make_user

pytestmark = pytest.mark.asyncio

STORED = "10000000000000000"
OTHER = "10000000000000001"
NAME = "Lords of the Fallen"


def _member(discord_id: str, activity: discord.BaseActivity | None):
    return SimpleNamespace(
        id=int(discord_id),
        bot=False,
        activities=[activity] if activity else [],
    )


def _playing(name: str, application_id: int | None):
    if application_id is None:
        return discord.Game(name=name)
    return discord.Activity(
        type=discord.ActivityType.playing,
        name=name,
        application_id=application_id,
    )


@asynccontextmanager
async def _use_db(db):
    yield db


def _patch_queue(monkeypatch) -> list[int]:
    queued: list[int] = []
    monkeypatch.setattr(
        "app.bot.reconcile.queue_enrichment",
        lambda game_id: queued.append(game_id),
    )
    return queued


async def _fire(db, discord_id: str, before, after) -> None:
    with patch.object(bot_main, "AsyncSessionLocal", lambda: _use_db(db)):
        await bot_main.on_presence_update(
            _member(discord_id, before),
            _member(discord_id, after),
        )


async def _alias(db, name: str) -> GameAlias:
    return (
        await db.execute(select(GameAlias).where(GameAlias.discord_process_name == name))
    ).scalar_one()


async def test_equal_names_store_the_first_id_and_do_not_queue(db, monkeypatch):
    user = await make_user(db)
    game = await make_game(db, NAME, enrichment_status=EnrichmentStatus.ENRICHED)
    alias = await make_alias(db, game.id, NAME)
    session = await make_session(
        db,
        user.discord_id,
        game.id,
        start_time=dt(hours_ago=1),
        status=SessionStatus.ONGOING,
        source=SessionSource.BOT,
    )
    queued = _patch_queue(monkeypatch)

    await _fire(db, user.discord_id, _playing(NAME, None), _playing(NAME, 10**16))

    await db.refresh(session)
    await db.refresh(alias)
    assert session.status == SessionStatus.ONGOING
    assert alias.application_id == STORED
    assert queued == []
    rows = (
        await db.execute(select(GameSession).where(GameSession.user_id == user.discord_id))
    ).scalars().all()
    assert len(list(rows)) == 1


async def test_the_same_id_again_does_not_rewrite(db, monkeypatch, caplog):
    user = await make_user(db)
    game = await make_game(db, NAME)
    alias = await make_alias(db, game.id, NAME)
    alias.application_id = STORED
    await db.commit()
    _patch_queue(monkeypatch)

    with caplog.at_level(logging.INFO):
        await _fire(db, user.discord_id, _playing(NAME, 10**16), _playing(NAME, 10**16))

    await db.refresh(alias)
    assert alias.application_id == STORED
    assert "presence.application_id_conflict" not in caplog.text


async def test_a_game_activity_stores_nothing(db, monkeypatch):
    user = await make_user(db)
    game = await make_game(db, NAME)
    alias = await make_alias(db, game.id, NAME)
    _patch_queue(monkeypatch)

    await _fire(db, user.discord_id, _playing(NAME, None), _playing(NAME, None))

    await db.refresh(alias)
    assert alias.application_id is None


async def test_self_heal_continue_stores_a_null_id(db, monkeypatch):
    user = await make_user(db)
    game = await make_game(db, NAME, enrichment_status=EnrichmentStatus.PENDING)
    alias = await make_alias(db, game.id, NAME)
    session = await make_session(
        db,
        user.discord_id,
        game.id,
        start_time=dt(hours_ago=1),
        status=SessionStatus.ONGOING,
        source=SessionSource.BOT,
    )
    queued = _patch_queue(monkeypatch)
    member = _member(user.discord_id, _playing(NAME, 10**16))
    guild = SimpleNamespace(
        members=[member],
        get_member=lambda uid: member if uid == int(user.discord_id) else None,
    )

    await run_self_healing(db, guilds=[guild])

    await db.refresh(session)
    await db.refresh(alias)
    assert session.status == SessionStatus.ONGOING
    assert alias.application_id == STORED
    assert queued == []


async def test_conflict_on_a_pending_start_queues_once(db, monkeypatch, caplog):
    user = await make_user(db)
    game = await make_game(db, NAME, enrichment_status=EnrichmentStatus.PENDING)
    alias = await make_alias(db, game.id, NAME)
    alias.application_id = STORED
    await db.commit()
    queued = _patch_queue(monkeypatch)

    with caplog.at_level(logging.INFO):
        await reconcile_user(
            db,
            user.discord_id,
            before_name=None,
            before_application_id=None,
            after_name=NAME,
            after_application_id=OTHER,
            gap=False,
        )

    await db.refresh(alias)
    await db.refresh(game)
    assert alias.application_id == STORED
    assert game.enrichment_status == EnrichmentStatus.PENDING
    assert queued == [game.id]
    record = next(
        row for row in caplog.records if row.message == "presence.application_id_conflict"
    )
    assert record.game_id == game.id
    assert record.alias_id == alias.id
    assert record.stored_application_id == STORED
    assert record.application_id == OTHER
    sessions = (
        await db.execute(select(GameSession).where(GameSession.user_id == user.discord_id))
    ).scalars().all()
    assert [row.game_id for row in sessions] == [game.id]


async def test_lost_insert_race_stores_the_id_on_the_surviving_alias(db, monkeypatch):
    user = await make_user(db)
    owner = await make_game(db, NAME, enrichment_status=EnrichmentStatus.PENDING)
    alias = await make_alias(db, owner.id, NAME)
    await db.commit()

    async def miss(_db, _name):
        return None

    monkeypatch.setattr("app.bot.session_manager.resolve_alias", miss)
    queued = _patch_queue(monkeypatch)

    await _start_and_commit(
        db,
        user.discord_id,
        NAME,
        startup=False,
        application_id=STORED,
    )

    await db.refresh(alias)
    assert alias.application_id == STORED
    assert queued == [owner.id]
    assert await resolve_alias(db, NAME) == owner.id


async def test_a_second_presence_with_another_id_uses_the_same_game(db, monkeypatch):
    first = await make_user(db, discord_id="111111111111111111")
    second = await make_user(db, discord_id="222222222222222222", username="other")
    game = await make_game(db, NAME, enrichment_status=EnrichmentStatus.ENRICHED)
    alias = await make_alias(db, game.id, NAME)
    alias.application_id = STORED
    await db.commit()
    _patch_queue(monkeypatch)

    await reconcile_user(
        db,
        first.discord_id,
        before_name=None,
        before_application_id=None,
        after_name=NAME,
        after_application_id=STORED,
        gap=False,
    )
    await reconcile_user(
        db,
        second.discord_id,
        before_name=None,
        before_application_id=None,
        after_name=NAME,
        after_application_id=OTHER,
        gap=False,
    )
    await reconcile_user(
        db,
        second.discord_id,
        before_name=NAME,
        before_application_id=OTHER,
        after_name=None,
        after_application_id=None,
        gap=False,
    )

    await db.refresh(alias)
    assert alias.application_id == STORED
    assert await resolve_alias(db, NAME) == game.id
    rows = (
        await db.execute(select(GameSession).order_by(GameSession.id))
    ).scalars().all()
    assert [row.game_id for row in rows] == [game.id, game.id]
    assert [row.status for row in rows] == [SessionStatus.ONGOING, SessionStatus.COMPLETED]
