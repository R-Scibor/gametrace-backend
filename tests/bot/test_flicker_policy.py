"""
tests/bot/test_flicker_policy.py

Unit tests for the flicker policy module.
Pure-decision functions: is_short_flicker and find_stitch_candidate.
"""
from datetime import UTC, datetime, timedelta

from app.bot.flicker_policy import find_stitch_candidate, is_short_flicker
from app.models.session import SessionSource, SessionStatus
from tests.factories import make_game, make_session, make_user

# ── is_short_flicker ──────────────────────────────────────────────────────────

def test_is_short_flicker_below_threshold():
    assert is_short_flicker(179) is True


def test_is_short_flicker_at_threshold():
    assert is_short_flicker(180) is False


def test_is_short_flicker_above_threshold():
    assert is_short_flicker(181) is False


# ── find_stitch_candidate ─────────────────────────────────────────────────────

async def test_find_stitch_candidate_returns_recent_bot_completed(db):
    user = await make_user(db)
    game = await make_game(db)
    now = datetime.now(UTC)
    session = await make_session(
        db, user.discord_id, game.id,
        start_time=now - timedelta(seconds=120),
        end_time=now - timedelta(seconds=60),
        status=SessionStatus.COMPLETED,
        source=SessionSource.BOT,
    )

    result = await find_stitch_candidate(db, user.discord_id, game.id)

    assert result is not None
    assert result.id == session.id


async def test_find_stitch_candidate_includes_flicker_rows(db):
    """Stitch must be able to find a row even when is_flicker=True."""
    user = await make_user(db)
    game = await make_game(db)
    now = datetime.now(UTC)
    session = await make_session(
        db, user.discord_id, game.id,
        start_time=now - timedelta(seconds=120),
        end_time=now - timedelta(seconds=60),
        status=SessionStatus.COMPLETED,
        source=SessionSource.BOT,
        is_flicker=True,
    )

    result = await find_stitch_candidate(db, user.discord_id, game.id)

    assert result is not None
    assert result.id == session.id


async def test_find_stitch_candidate_returns_most_recent(db):
    """When two BOT COMPLETED sessions are in the window, return the latest."""
    user = await make_user(db)
    game = await make_game(db)
    now = datetime.now(UTC)

    older = await make_session(
        db, user.discord_id, game.id,
        start_time=now - timedelta(seconds=170),
        end_time=now - timedelta(seconds=120),
        status=SessionStatus.COMPLETED,
        source=SessionSource.BOT,
    )
    newer = await make_session(
        db, user.discord_id, game.id,
        start_time=now - timedelta(seconds=100),
        end_time=now - timedelta(seconds=60),
        status=SessionStatus.COMPLETED,
        source=SessionSource.BOT,
    )

    result = await find_stitch_candidate(db, user.discord_id, game.id)

    assert result is not None
    assert result.id == newer.id
    assert result.id != older.id


async def test_find_stitch_candidate_none_for_manual_source(db):
    user = await make_user(db)
    game = await make_game(db)
    now = datetime.now(UTC)
    await make_session(
        db, user.discord_id, game.id,
        start_time=now - timedelta(seconds=120),
        end_time=now - timedelta(seconds=60),
        status=SessionStatus.COMPLETED,
        source=SessionSource.MANUAL,
    )

    result = await find_stitch_candidate(db, user.discord_id, game.id)

    assert result is None


async def test_find_stitch_candidate_none_for_ongoing_status(db):
    user = await make_user(db)
    game = await make_game(db)
    now = datetime.now(UTC)
    await make_session(
        db, user.discord_id, game.id,
        start_time=now - timedelta(seconds=120),
        end_time=None,
        status=SessionStatus.ONGOING,
        source=SessionSource.BOT,
    )

    result = await find_stitch_candidate(db, user.discord_id, game.id)

    assert result is None


async def test_find_stitch_candidate_none_for_error_status(db):
    user = await make_user(db)
    game = await make_game(db)
    now = datetime.now(UTC)
    await make_session(
        db, user.discord_id, game.id,
        start_time=now - timedelta(seconds=120),
        end_time=now - timedelta(seconds=60),
        status=SessionStatus.ERROR,
        source=SessionSource.BOT,
    )

    result = await find_stitch_candidate(db, user.discord_id, game.id)

    assert result is None


async def test_find_stitch_candidate_none_for_soft_deleted(db):
    user = await make_user(db)
    game = await make_game(db)
    now = datetime.now(UTC)
    await make_session(
        db, user.discord_id, game.id,
        start_time=now - timedelta(seconds=120),
        end_time=now - timedelta(seconds=60),
        status=SessionStatus.COMPLETED,
        source=SessionSource.BOT,
        deleted_at=now - timedelta(seconds=10),
    )

    result = await find_stitch_candidate(db, user.discord_id, game.id)

    assert result is None


async def test_find_stitch_candidate_none_when_outside_window(db):
    """end_time older than stitch window (default 180s) → no match."""
    user = await make_user(db)
    game = await make_game(db)
    now = datetime.now(UTC)
    await make_session(
        db, user.discord_id, game.id,
        start_time=now - timedelta(minutes=12),
        end_time=now - timedelta(minutes=10),  # 600s ago, well outside 180s window
        status=SessionStatus.COMPLETED,
        source=SessionSource.BOT,
    )

    result = await find_stitch_candidate(db, user.discord_id, game.id)

    assert result is None


async def test_find_stitch_candidate_none_at_exact_window_boundary(db):
    """Dropout gap of exactly STITCH_WINDOW (180s) does not stitch — strict >."""
    user = await make_user(db)
    game = await make_game(db)
    now = datetime.now(UTC)
    await make_session(
        db, user.discord_id, game.id,
        start_time=now - timedelta(seconds=240),
        end_time=now - timedelta(seconds=180),
        status=SessionStatus.COMPLETED,
        source=SessionSource.BOT,
    )

    result = await find_stitch_candidate(db, user.discord_id, game.id)

    assert result is None


async def test_find_stitch_candidate_matches_just_inside_window(db):
    """Dropout gap of 179s (end_time 1s inside window) → stitches."""
    user = await make_user(db)
    game = await make_game(db)
    now = datetime.now(UTC)
    session = await make_session(
        db, user.discord_id, game.id,
        start_time=now - timedelta(seconds=240),
        end_time=now - timedelta(seconds=179),
        status=SessionStatus.COMPLETED,
        source=SessionSource.BOT,
    )

    result = await find_stitch_candidate(db, user.discord_id, game.id)

    assert result is not None
    assert result.id == session.id


async def test_find_stitch_candidate_none_for_different_game(db):
    user = await make_user(db)
    game_a = await make_game(db, "Game A")
    game_b = await make_game(db, "Game B")
    now = datetime.now(UTC)
    await make_session(
        db, user.discord_id, game_a.id,
        start_time=now - timedelta(seconds=120),
        end_time=now - timedelta(seconds=60),
        status=SessionStatus.COMPLETED,
        source=SessionSource.BOT,
    )

    result = await find_stitch_candidate(db, user.discord_id, game_b.id)

    assert result is None
