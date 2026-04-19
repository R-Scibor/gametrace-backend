"""Unit tests for app.services.stats.summary_for_user.

The HTTP handler tests in tests/api/test_stats_summary.py already cover the
endpoint contract. These tests exist to lock the helper's behaviour in place
so it can be reused by the weekly-report Celery task without drift.
"""
from app.models.session import SessionStatus
from app.services.stats import summary_for_user
from tests.factories import dt, make_game, make_pref, make_session


async def test_summary_for_user_excludes_ignored_game(db, user):
    ignored_game = await make_game(db, primary_name="Ignored")
    normal_game = await make_game(db, primary_name="Normal")
    await make_pref(db, user.discord_id, ignored_game.id, is_ignored=True)

    await make_session(db, user.discord_id, ignored_game.id, dt(hours_ago=3), dt(hours_ago=2))
    await make_session(db, user.discord_id, normal_game.id, dt(hours_ago=5), dt(hours_ago=4))

    result = await summary_for_user(db, user, days=7)

    assert result.total_seconds == 3600
    assert [entry.game_name for entry in result.per_game] == ["Normal"]


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
