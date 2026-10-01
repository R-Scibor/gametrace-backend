"""Decision table for reconcile_user. The caller holds the per-user lock."""

from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.bot.reconcile import reconcile_user
from app.bot.session_manager import error_session
from app.models.game import EnrichmentStatus, Game
from app.models.session import GameSession, SessionSource, SessionStatus
from tests.factories import dt, make_alias, make_game, make_session, make_user


async def _bot_ongoing(db, user, game, *, hours_ago: float = 1):
    return await make_session(
        db,
        user.discord_id,
        game.id,
        start_time=dt(hours_ago=hours_ago),
        status=SessionStatus.ONGOING,
        source=SessionSource.BOT,
    )


async def _sessions(db, user_id: str) -> list[GameSession]:
    result = await db.execute(
        select(GameSession)
        .where(GameSession.user_id == user_id)
        .order_by(GameSession.id)
    )
    return list(result.scalars().all())


def _patch_queue(monkeypatch) -> list[int]:
    queued: list[int] = []
    monkeypatch.setattr("app.bot.reconcile.queue_enrichment", lambda game_id: queued.append(game_id))
    return queued


async def test_stop_with_no_open_row_writes_nothing(db, monkeypatch):
    user = await make_user(db)
    queued = _patch_queue(monkeypatch)

    await reconcile_user(
        db, user.discord_id, before_name="Hades.exe", after_name=None, gap=False
    )

    assert await _sessions(db, user.discord_id) == []
    assert queued == []


async def test_matching_stop_completes(db, monkeypatch):
    user = await make_user(db)
    game = await make_game(db, "Hades")
    await make_alias(db, game.id, "Hades.exe")
    session = await _bot_ongoing(db, user, game)
    queued = _patch_queue(monkeypatch)

    await reconcile_user(
        db, user.discord_id, before_name="Hades.exe", after_name=None, gap=False
    )

    await db.refresh(session)
    assert session.status == SessionStatus.COMPLETED
    assert session.end_time is not None
    assert session.notes is None
    assert queued == []


async def test_mismatched_stop_errors_with_the_presence_note(db):
    user = await make_user(db)
    game = await make_game(db, "Hades")
    await make_alias(db, game.id, "Hades.exe")
    session = await _bot_ongoing(db, user, game)

    await reconcile_user(
        db, user.discord_id, before_name="Other.exe", after_name=None, gap=False
    )

    await db.refresh(session)
    assert session.status == SessionStatus.ERROR
    assert session.end_time is None
    assert session.notes == "Presence: open session did not match activity 'Other.exe'."


async def test_repeat_start_of_the_same_game_is_untouched(db, monkeypatch):
    user = await make_user(db)
    game = await make_game(db, "Hades")
    await make_alias(db, game.id, "Hades.exe")
    session = await _bot_ongoing(db, user, game)
    original_start = session.start_time
    queued = _patch_queue(monkeypatch)

    await reconcile_user(
        db, user.discord_id, before_name=None, after_name="Hades.exe", gap=False
    )

    await db.refresh(session)
    assert session.status == SessionStatus.ONGOING
    assert session.start_time == original_start
    assert session.notes is None
    assert len(await _sessions(db, user.discord_id)) == 1
    assert queued == []


async def test_start_of_a_different_game_errors_then_starts(db, monkeypatch):
    user = await make_user(db)
    old = await make_game(db, "Hades")
    await make_alias(db, old.id, "Hades.exe")
    session = await _bot_ongoing(db, user, old)
    queued = _patch_queue(monkeypatch)

    await reconcile_user(
        db, user.discord_id, before_name=None, after_name="Minecraft", gap=False
    )

    await db.refresh(session)
    assert session.status == SessionStatus.ERROR
    assert session.end_time is None
    assert session.notes == "Presence: open session did not match activity 'Minecraft'."
    rows = await _sessions(db, user.discord_id)
    opened = next(row for row in rows if row.status == SessionStatus.ONGOING)
    new_game = await db.get(Game, opened.game_id)
    assert new_game is not None
    assert new_game.primary_name == "Minecraft"
    assert queued == [new_game.id]


async def test_same_game_id_string_change_with_open_row_is_untouched(db, monkeypatch):
    user = await make_user(db)
    game = await make_game(db, "Hades")
    await make_alias(db, game.id, "Hades")
    await make_alias(db, game.id, "Hades.exe")
    session = await _bot_ongoing(db, user, game)
    queued = _patch_queue(monkeypatch)

    await reconcile_user(
        db,
        user.discord_id,
        before_name="Hades",
        after_name="Hades.exe",
        gap=False,
    )

    await db.refresh(session)
    assert session.status == SessionStatus.ONGOING
    assert session.notes is None
    assert len(await _sessions(db, user.discord_id)) == 1
    assert queued == []


async def test_same_game_id_string_change_with_no_open_row_starts(db, monkeypatch):
    user = await make_user(db)
    game = await make_game(db, "Hades", EnrichmentStatus.PENDING)
    await make_alias(db, game.id, "Hades")
    await make_alias(db, game.id, "Hades.exe")
    queued = _patch_queue(monkeypatch)

    await reconcile_user(
        db,
        user.discord_id,
        before_name="Hades",
        after_name="Hades.exe",
        gap=False,
    )

    rows = await _sessions(db, user.discord_id)
    assert len(rows) == 1
    assert rows[0].status == SessionStatus.ONGOING
    assert rows[0].game_id == game.id
    assert queued == [game.id]


async def test_switch_matching_before_completes_then_starts(db):
    user = await make_user(db)
    old = await make_game(db, "Hades")
    await make_alias(db, old.id, "Hades.exe")
    session = await _bot_ongoing(db, user, old)

    await reconcile_user(
        db,
        user.discord_id,
        before_name="Hades.exe",
        after_name="Minecraft",
        gap=False,
    )

    await db.refresh(session)
    assert session.status == SessionStatus.COMPLETED
    assert session.notes is None
    opened = next(
        row for row in await _sessions(db, user.discord_id) if row.status == SessionStatus.ONGOING
    )
    new_game = await db.get(Game, opened.game_id)
    assert new_game is not None
    assert new_game.primary_name == "Minecraft"


async def test_switch_with_no_open_row_does_not_call_error_session(db, monkeypatch):
    user = await make_user(db)
    calls = {"error": 0}
    real = error_session

    async def wrapped(*args, **kwargs):
        calls["error"] += 1
        return await real(*args, **kwargs)

    monkeypatch.setattr("app.bot.reconcile.error_session", wrapped)

    await reconcile_user(
        db,
        user.discord_id,
        before_name="Hades.exe",
        after_name="Minecraft",
        gap=False,
    )

    assert calls["error"] == 0
    rows = await _sessions(db, user.discord_id)
    assert len(rows) == 1
    assert rows[0].status == SessionStatus.ONGOING


async def test_switch_whose_open_row_does_not_match_before_errors_then_starts(db):
    user = await make_user(db)
    open_game = await make_game(db, "Celeste")
    await make_alias(db, open_game.id, "Celeste.exe")
    hades = await make_game(db, "Hades")
    await make_alias(db, hades.id, "Hades")
    await make_alias(db, hades.id, "Hades.exe")
    session = await _bot_ongoing(db, user, open_game)

    await reconcile_user(
        db,
        user.discord_id,
        before_name="Hades",
        after_name="Hades.exe",
        gap=False,
    )

    await db.refresh(session)
    assert session.status == SessionStatus.ERROR
    assert session.end_time is None
    assert session.notes == "Presence: open session did not match activity 'Hades'."
    opened = next(
        row for row in await _sessions(db, user.discord_id) if row.status == SessionStatus.ONGOING
    )
    assert opened.game_id == hades.id


async def test_return_to_the_open_game_errors_the_stale_row(db, monkeypatch):
    """Open row is Hades, Celeste just closed, and Hades is playing again.

    The open row is not the session for the game that just closed, so it is
    marked ERROR even though the current activity is the same catalog game.
    """
    user = await make_user(db)
    hades = await make_game(db, "Hades", EnrichmentStatus.ENRICHED)
    await make_alias(db, hades.id, "Hades.exe")
    celeste = await make_game(db, "Celeste")
    await make_alias(db, celeste.id, "Celeste.exe")
    session = await _bot_ongoing(db, user, hades)
    queued = _patch_queue(monkeypatch)

    await reconcile_user(
        db,
        user.discord_id,
        before_name="Celeste.exe",
        after_name="Hades.exe",
        gap=False,
    )

    await db.refresh(session)
    assert session.status == SessionStatus.ERROR
    assert session.end_time is None
    assert session.notes == "Presence: open session did not match activity 'Celeste.exe'."
    opened = next(
        row for row in await _sessions(db, user.discord_id) if row.status == SessionStatus.ONGOING
    )
    assert opened.id != session.id
    assert opened.game_id == hades.id
    assert queued == []


async def test_startup_alias_keeps_the_row_when_the_title_differs(db, monkeypatch):
    user = await make_user(db)
    game = await make_game(db, "Hades")
    await make_alias(db, game.id, "Hades.exe")
    session = await _bot_ongoing(db, user, game, hours_ago=13)
    original_start = session.start_time
    queued = _patch_queue(monkeypatch)
    created = AsyncMock(side_effect=AssertionError("keep must not insert"))
    monkeypatch.setattr("app.bot.reconcile.get_or_create_game", created)

    await reconcile_user(
        db,
        user.discord_id,
        before_name=None,
        after_name="Hades.exe",
        gap=True,
    )

    await db.refresh(session)
    assert session.status == SessionStatus.ONGOING
    assert session.start_time == original_start
    assert session.notes is None
    assert len(await _sessions(db, user.discord_id)) == 1
    assert queued == []
    created.assert_not_called()


async def test_startup_over_12h_on_a_different_game_uses_the_switch_note(db, monkeypatch):
    user = await make_user(db)
    old = await make_game(db, "Hades")
    await make_alias(db, old.id, "Hades.exe")
    session = await _bot_ongoing(db, user, old, hours_ago=13)
    queued = _patch_queue(monkeypatch)

    async def no_stitch(*_args, **_kwargs):
        raise AssertionError("startup must not stitch")

    monkeypatch.setattr("app.bot.reconcile.start_or_resume_session", no_stitch)

    await reconcile_user(
        db,
        user.discord_id,
        before_name=None,
        after_name="Minecraft",
        gap=True,
    )

    await db.refresh(session)
    assert session.status == SessionStatus.ERROR
    assert session.end_time is None
    assert session.notes == (
        "Self-Healing: bot restarted, player switched from 'Hades' to 'Minecraft'."
    )
    opened = next(
        row for row in await _sessions(db, user.discord_id) if row.status == SessionStatus.ONGOING
    )
    assert opened.id != session.id
    new_game = await db.get(Game, opened.game_id)
    assert new_game is not None
    assert new_game.primary_name == "Minecraft"
    assert queued == [new_game.id]


async def test_startup_over_12h_and_not_playing_errors_without_a_new_row(db, monkeypatch):
    user = await make_user(db)
    game = await make_game(db, "Hades")
    session = await _bot_ongoing(db, user, game, hours_ago=13)
    queued = _patch_queue(monkeypatch)

    await reconcile_user(
        db, user.discord_id, before_name=None, after_name=None, gap=True
    )

    await db.refresh(session)
    assert session.status == SessionStatus.ERROR
    assert session.end_time is None
    assert session.notes == (
        "Self-Healing: session exceeded 12h threshold after bot restart — possible stale session."
    )
    assert len(await _sessions(db, user.discord_id)) == 1
    assert queued == []


async def test_startup_not_playing_within_12h_uses_the_idle_note(db):
    user = await make_user(db)
    game = await make_game(db, "Hades")
    session = await _bot_ongoing(db, user, game, hours_ago=1)

    await reconcile_user(
        db, user.discord_id, before_name=None, after_name=None, gap=True
    )

    await db.refresh(session)
    assert session.status == SessionStatus.ERROR
    assert session.notes == "Self-Healing: bot restarted, player is no longer in-game."


async def test_startup_missing_member_wins_over_the_12h_check(db):
    user = await make_user(db)
    game = await make_game(db, "Hades")
    session = await _bot_ongoing(db, user, game, hours_ago=13)

    await reconcile_user(
        db,
        user.discord_id,
        before_name=None,
        after_name=None,
        gap=True,
        member_found=False,
    )

    await db.refresh(session)
    assert session.status == SessionStatus.ERROR
    assert session.notes == "Self-Healing: user not found in any guild after bot restart."
    assert "12h" not in session.notes


async def test_startup_purge_errors_a_matching_activity_and_does_not_start(db, monkeypatch):
    user = await make_user(
        db,
        deletion_requested_at=dt(hours_ago=1),
        purge_at=dt(hours_from_now=24 * 7),
    )
    game = await make_game(db, "Hades")
    await make_alias(db, game.id, "Hades.exe")
    session = await _bot_ongoing(db, user, game)
    queued = _patch_queue(monkeypatch)

    await reconcile_user(
        db,
        user.discord_id,
        before_name=None,
        after_name="Hades.exe",
        gap=True,
    )

    await db.refresh(session)
    assert session.status == SessionStatus.ERROR
    assert session.notes == "Self-Healing: account scheduled for deletion."
    assert len(await _sessions(db, user.discord_id)) == 1
    assert queued == []


async def test_startup_case_difference_is_a_switch(db):
    user = await make_user(db)
    game = await make_game(db, "Hades")
    await make_alias(db, game.id, "Hades")
    session = await _bot_ongoing(db, user, game)

    await reconcile_user(
        db, user.discord_id, before_name=None, after_name="hades", gap=True
    )

    await db.refresh(session)
    assert session.status == SessionStatus.ERROR
    assert "switched from 'Hades' to 'hades'" in session.notes
    opened = next(
        row for row in await _sessions(db, user.discord_id) if row.status == SessionStatus.ONGOING
    )
    new_game = await db.get(Game, opened.game_id)
    assert new_game is not None
    assert new_game.primary_name == "hades"
    assert new_game.id != game.id


async def test_startup_gone_row_does_not_start(db):
    user = await make_user(db)

    await reconcile_user(
        db, user.discord_id, before_name=None, after_name="Minecraft", gap=True
    )

    assert await _sessions(db, user.discord_id) == []


async def test_prior_name_is_unknown_when_the_game_row_is_missing(db, monkeypatch):
    from app.bot.reconcile import _prior_name

    async def no_game(*_args, **_kwargs):
        return None

    monkeypatch.setattr(db, "get", no_game)
    assert await _prior_name(db, 1) == "unknown"


async def test_enriched_game_does_not_enqueue(db, monkeypatch):
    user = await make_user(db)
    game = await make_game(db, "Hades", EnrichmentStatus.ENRICHED)
    await make_alias(db, game.id, "Hades.exe")
    queued = _patch_queue(monkeypatch)

    await reconcile_user(
        db, user.discord_id, before_name=None, after_name="Hades.exe", gap=False
    )

    assert len(await _sessions(db, user.discord_id)) == 1
    assert queued == []


async def test_needs_review_game_does_not_enqueue(db, monkeypatch):
    user = await make_user(db)
    game = await make_game(db, "Hades", EnrichmentStatus.NEEDS_REVIEW)
    await make_alias(db, game.id, "Hades.exe")
    queued = _patch_queue(monkeypatch)

    await reconcile_user(
        db, user.discord_id, before_name=None, after_name="Hades.exe", gap=False
    )

    assert len(await _sessions(db, user.discord_id)) == 1
    assert queued == []


async def test_enqueue_failure_does_not_remove_the_session(db, monkeypatch):
    user = await make_user(db)

    def boom(_game_id: int) -> None:
        raise RuntimeError("redis down")

    monkeypatch.setattr("app.bot.reconcile.queue_enrichment", boom)

    await reconcile_user(
        db, user.discord_id, before_name=None, after_name="Minecraft", gap=False
    )

    rows = await _sessions(db, user.discord_id)
    assert len(rows) == 1
    assert rows[0].status == SessionStatus.ONGOING


async def test_none_from_start_rolls_back_the_stub_and_does_not_enqueue(db, monkeypatch):
    user = await make_user(db)
    game = await make_game(db, "Hades")
    await make_session(
        db,
        user.discord_id,
        game.id,
        dt(hours_ago=1),
        dt(hours_from_now=1),
        source=SessionSource.MANUAL,
    )
    await db.commit()
    queued = _patch_queue(monkeypatch)
    before = (
        await db.execute(select(func.count()).select_from(Game))
    ).scalar_one()

    user_id = user.discord_id
    await reconcile_user(
        db, user_id, before_name=None, after_name="BrandNew", gap=False
    )

    after = (
        await db.execute(select(func.count()).select_from(Game))
    ).scalar_one()
    assert after == before
    assert await _sessions(db, user_id) != []
    assert all(row.status != SessionStatus.ONGOING for row in await _sessions(db, user_id))
    assert queued == []


async def test_different_game_unique_race_does_not_enqueue(db, monkeypatch):
    user = await make_user(db)
    game = await make_game(db, "Hades")
    existing = await _bot_ongoing(db, user, game)
    await db.commit()
    existing_id = existing.id
    queued = _patch_queue(monkeypatch)

    async def hidden(_db, _user_id, *, populate_existing=False):
        return None

    monkeypatch.setattr("app.bot.reconcile.get_ongoing_session", hidden)

    with pytest.raises(IntegrityError):
        await reconcile_user(
            db, user.discord_id, before_name=None, after_name="Minecraft", gap=False
        )

    minecraft = (
        await db.execute(select(Game).where(Game.primary_name == "Minecraft"))
    ).scalar_one_or_none()
    assert minecraft is None
    ongoing = await db.get(GameSession, existing_id)
    assert ongoing is not None
    assert ongoing.status == SessionStatus.ONGOING
    assert queued == []
