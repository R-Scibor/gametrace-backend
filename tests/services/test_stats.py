"""Unit tests for app.services.stats.summary_for_user.

The HTTP handler tests in tests/api/test_stats_summary.py already cover the
endpoint contract. These tests exist to lock the helper's behaviour in place
so it can be reused by the weekly-report Celery task without drift.
"""
from datetime import datetime

from app.models.session import SessionStatus
from app.services.stats import _split_session_across_cells, summary_for_user
from tests.factories import dt, make_game, make_pref, make_session


# ── _split_session_across_cells (pure) ────────────────────────────────────────

def test_split_within_single_hour():
    start = datetime(2026, 4, 15, 14, 10)  # Wed
    assert _split_session_across_cells(start, 600) == [(2, 14, 600)]


def test_split_across_hour_boundary():
    start = datetime(2026, 4, 15, 14, 30)  # Wed
    assert _split_session_across_cells(start, 3600) == [(2, 14, 1800), (2, 15, 1800)]


def test_split_across_midnight_changes_dow():
    start = datetime(2026, 4, 15, 23, 0)  # Wed 23:00
    assert _split_session_across_cells(start, 3 * 3600) == [
        (2, 23, 3600),  # Wed 23:00
        (3, 0, 3600),   # Thu 00:00
        (3, 1, 3600),   # Thu 01:00
    ]


def test_split_zero_duration_is_empty():
    assert _split_session_across_cells(datetime(2026, 4, 15, 14, 0), 0) == []


def test_split_sunday_to_monday_wraps_dow():
    start = datetime(2026, 4, 19, 23, 30)  # Sunday → dow=6
    result = _split_session_across_cells(start, 3600)
    assert result == [(6, 23, 1800), (0, 0, 1800)]  # wraps Sun→Mon


async def test_summary_for_user_excludes_ignored_game(db, user):
    ignored_game = await make_game(db, primary_name="Ignored")
    normal_game = await make_game(db, primary_name="Normal")
    await make_pref(db, user.discord_id, ignored_game.id, is_ignored=True)

    await make_session(db, user.discord_id, ignored_game.id, dt(hours_ago=3), dt(hours_ago=2))
    await make_session(db, user.discord_id, normal_game.id, dt(hours_ago=5), dt(hours_ago=4))

    result = await summary_for_user(db, user, days=7)

    assert result.total_seconds == 3600
    assert [entry.game_name for entry in result.per_game] == ["Normal"]


async def test_summary_excludes_flicker_session(db, user):
    """A flicker session must not contribute to totals."""
    game = await make_game(db)
    await make_session(db, user.discord_id, game.id, dt(hours_ago=3), dt(hours_ago=2))
    await make_session(
        db, user.discord_id, game.id, dt(hours_ago=5), dt(hours_ago=4), is_flicker=True
    )

    result = await summary_for_user(db, user, days=7)

    # Only the non-flicker session (1 hour) should be counted.
    assert result.total_seconds == 3600
    assert len(result.per_game) == 1


async def test_summary_for_user_includes_pending_errors(db, user):
    game = await make_game(db)
    await make_session(
        db,
        user.discord_id,
        game.id,
        dt(hours_ago=3),
        status=SessionStatus.ERROR,
        notes="12h threshold",
    )

    result = await summary_for_user(db, user, days=7)

    assert len(result.pending_errors) == 1
    assert result.pending_errors[0].notes == "12h threshold"
    assert result.total_seconds == 0
