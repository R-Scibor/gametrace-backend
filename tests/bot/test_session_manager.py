"""
tests/bot/test_session_manager.py

Phase 2 — DB-layer functions used by the Discord bot.
Called directly with the test `db` fixture — no HTTP client, no Discord connection.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy import select

from app.bot.session_manager import (
    complete_session,
    error_session,
    get_ongoing_session,
    get_or_create_game,
    get_user_if_tracked,
    start_session,
)
from app.models.game import EnrichmentStatus, Game, GameAlias, UserGamePreference
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


async def test_start_session_on_needs_review_game_creates_inbox_pref(db):
    user = await make_user(db)
    game = await make_game(db, "launcher.exe", EnrichmentStatus.NEEDS_REVIEW)

    await start_session(db, user.discord_id, game.id)

    pref = (
        await db.execute(
            select(UserGamePreference).where(
                UserGamePreference.user_id == user.discord_id,
                UserGamePreference.game_id == game.id,
            )
        )
    ).scalar_one()
    assert pref.is_accepted is False


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


async def test_get_ongoing_session_returns_newest_when_duplicates(caplog):
    newer = MagicMock(id=2)
    older = MagicMock(id=1)
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [newer, older]
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result)

    result = await get_ongoing_session(mock_db, "user-a")

    assert result is newer
    assert "duplicate ongoing" in caplog.text.lower()


async def test_start_session_returns_existing_on_unique_index_race(db):
    user = await make_user(db)
    game = await make_game(db)
    existing = await make_session(
        db,
        user.discord_id,
        game.id,
        start_time=dt(hours_ago=1),
        status=SessionStatus.ONGOING,
        source=SessionSource.BOT,
    )
    await db.commit()

    other_game = await make_game(db, "Other Game")
    result = await start_session(db, user.discord_id, other_game.id)

    assert result.id == existing.id


# ── complete_session flicker flagging ─────────────────────────────────────────

async def test_complete_session_short_bot_flagged_as_flicker(db):
    """BOT session shorter than threshold → is_flicker=True after completion."""
    user = await make_user(db)
    game = await make_game(db)
    session = await make_session(
        db, user.discord_id, game.id,
        start_time=datetime.now(timezone.utc) - timedelta(seconds=60),
        end_time=None,
        status=SessionStatus.ONGOING,
        source=SessionSource.BOT,
    )

    completed = await complete_session(db, session)

    assert completed.is_flicker is True


async def test_complete_session_long_bot_not_flagged_as_flicker(db):
    """BOT session longer than threshold → is_flicker=False after completion."""
    user = await make_user(db)
    game = await make_game(db)
    session = await make_session(
        db, user.discord_id, game.id,
        start_time=dt(hours_ago=2),
        end_time=None,
        status=SessionStatus.ONGOING,
        source=SessionSource.BOT,
    )

    completed = await complete_session(db, session)

    assert completed.is_flicker is False


async def test_complete_session_short_manual_not_flagged_as_flicker(db):
    """MANUAL session shorter than threshold → is_flicker=False (source guard)."""
    user = await make_user(db)
    game = await make_game(db)
    session = await make_session(
        db, user.discord_id, game.id,
        start_time=datetime.now(timezone.utc) - timedelta(seconds=60),
        end_time=None,
        status=SessionStatus.ONGOING,
        source=SessionSource.MANUAL,
    )

    completed = await complete_session(db, session)

    assert completed.is_flicker is False


# ── start_or_resume_session ───────────────────────────────────────────────────

async def test_start_or_resume_session_reopens_recent_bot_session(db):
    """Recent same-game COMPLETED BOT row within stitch window → reopened."""
    from app.bot.session_manager import start_or_resume_session

    user = await make_user(db)
    game = await make_game(db)
    candidate = await make_session(
        db, user.discord_id, game.id,
        start_time=datetime.now(timezone.utc) - timedelta(seconds=90),
        end_time=datetime.now(timezone.utc) - timedelta(seconds=30),
        status=SessionStatus.COMPLETED,
        source=SessionSource.BOT,
        is_flicker=True,  # proves the flag is cleared on reopen
    )
    candidate_id = candidate.id

    result = await start_or_resume_session(db, user.discord_id, game.id)

    assert result.id == candidate_id
    assert result.status == SessionStatus.ONGOING
    assert result.end_time is None
    assert result.duration_seconds is None
    assert result.is_flicker is False


async def test_start_or_resume_session_no_candidate_creates_new(db):
    """No stitch candidate → creates a fresh ONGOING BOT session."""
    from app.bot.session_manager import start_or_resume_session

    user = await make_user(db)
    game = await make_game(db)

    result = await start_or_resume_session(db, user.discord_id, game.id)

    assert result.status == SessionStatus.ONGOING
    assert result.source == SessionSource.BOT
    assert result.end_time is None


async def test_start_or_resume_session_ignores_manual_source(db):
    """Same-game MANUAL COMPLETED row within window → NOT reopened; new BOT row created."""
    from app.bot.session_manager import start_or_resume_session

    user = await make_user(db)
    game = await make_game(db)
    manual_end = datetime.now(timezone.utc) - timedelta(seconds=30)
    manual = await make_session(
        db, user.discord_id, game.id,
        start_time=datetime.now(timezone.utc) - timedelta(seconds=90),
        end_time=manual_end,
        status=SessionStatus.COMPLETED,
        source=SessionSource.MANUAL,
    )
    manual_id = manual.id

    result = await start_or_resume_session(db, user.discord_id, game.id)

    # New row must be created, not the MANUAL one
    assert result.id != manual_id
    assert result.status == SessionStatus.ONGOING
    assert result.source == SessionSource.BOT

    # MANUAL row must remain untouched
    await db.refresh(manual)
    assert manual.status == SessionStatus.COMPLETED
    assert manual.end_time is not None


async def test_start_or_resume_session_outside_window_creates_new(db):
    """Same-game BOT COMPLETED row older than stitch window → NOT reopened; new row."""
    from app.bot.session_manager import start_or_resume_session

    user = await make_user(db)
    game = await make_game(db)
    await make_session(
        db, user.discord_id, game.id,
        start_time=datetime.now(timezone.utc) - timedelta(minutes=15),
        end_time=datetime.now(timezone.utc) - timedelta(minutes=10),
        status=SessionStatus.COMPLETED,
        source=SessionSource.BOT,
    )

    result = await start_or_resume_session(db, user.discord_id, game.id)

    assert result.status == SessionStatus.ONGOING
    assert result.source == SessionSource.BOT
