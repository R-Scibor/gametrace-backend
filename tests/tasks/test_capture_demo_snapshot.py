"""Snapshot capture script — unit tests.

Async tests call capture(db, source_discord_id) directly so the rollback
fixture keeps the test DB clean.
"""
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.models.demo_seed import DemoSeedPreference, DemoSeedSession
from app.models.session import SessionSource, SessionStatus
from app.scripts.capture_demo_snapshot import capture, capture_with_confirmation
from tests.factories import make_game, make_pref, make_session, make_user


async def _build_source_user(db):
    """A user with a mix of session states plus preferences."""
    source_user = await make_user(db, discord_id="333333333333333333", username="source")
    game = await make_game(db, primary_name="Captured Game")
    now = datetime.now(UTC)

    completed = await make_session(
        db, source_user.discord_id, game.id,
        now - timedelta(days=5), now - timedelta(days=5) + timedelta(hours=2),
        status=SessionStatus.COMPLETED,
        source=SessionSource.BOT,
        notes="should never be copied",
    )
    deleted = await make_session(
        db, source_user.discord_id, game.id,
        now - timedelta(days=4), now - timedelta(days=4) + timedelta(hours=1),
        status=SessionStatus.COMPLETED,
        deleted_at=now - timedelta(days=1),
    )
    flicker = await make_session(
        db, source_user.discord_id, game.id,
        now - timedelta(days=3), now - timedelta(days=3) + timedelta(minutes=5),
        status=SessionStatus.COMPLETED,
        is_flicker=True,
    )
    ongoing = await make_session(
        db, source_user.discord_id, game.id,
        now - timedelta(hours=1),
        status=SessionStatus.ONGOING,
    )
    error = await make_session(
        db, source_user.discord_id, game.id,
        now - timedelta(hours=6),
        status=SessionStatus.ERROR,
        notes="self-healing: bot restarted mid-session",
    )
    pref = await make_pref(db, source_user.discord_id, game.id, is_ignored=True)

    return source_user, game, {
        "completed": completed,
        "deleted": deleted,
        "flicker": flicker,
        "ongoing": ongoing,
        "error": error,
        "pref": pref,
    }


async def test_captures_only_eligible_sessions_and_preferences(db):
    source_user, game, rows = await _build_source_user(db)

    sessions_captured, preferences_captured = await capture(db, source_user.discord_id)

    assert sessions_captured == 1
    assert preferences_captured == 1

    seed_sessions = (await db.execute(select(DemoSeedSession))).scalars().all()
    assert len(seed_sessions) == 1
    seed = seed_sessions[0]
    assert seed.game_id == game.id
    assert seed.status == SessionStatus.COMPLETED.value
    assert seed.start_time == rows["completed"].start_time
    assert seed.end_time == rows["completed"].end_time
    assert all(s.status != SessionStatus.ERROR.value for s in seed_sessions)

    seed_prefs = (await db.execute(select(DemoSeedPreference))).scalars().all()
    assert len(seed_prefs) == 1
    assert seed_prefs[0].game_id == game.id
    assert seed_prefs[0].is_ignored is True


async def test_notes_column_is_never_copied(db):
    source_user, game, rows = await _build_source_user(db)

    await capture(db, source_user.discord_id)

    seed = (await db.execute(select(DemoSeedSession))).scalars().one()
    assert not hasattr(seed, "notes")


async def test_other_users_rows_are_never_captured(db):
    source_user, game, rows = await _build_source_user(db)

    other_user = await make_user(db, discord_id="444444444444444444", username="other")
    other_game = await make_game(db, primary_name="Other Game")
    now = datetime.now(UTC)
    await make_session(
        db, other_user.discord_id, other_game.id,
        now - timedelta(days=2), now - timedelta(days=2) + timedelta(hours=1),
        status=SessionStatus.COMPLETED,
    )
    await make_pref(db, other_user.discord_id, other_game.id, is_ignored=True)

    await capture(db, source_user.discord_id)

    seed_sessions = (await db.execute(select(DemoSeedSession))).scalars().all()
    assert len(seed_sessions) == 1
    assert seed_sessions[0].game_id == game.id

    seed_prefs = (await db.execute(select(DemoSeedPreference))).scalars().all()
    assert len(seed_prefs) == 1
    assert seed_prefs[0].game_id == game.id


async def test_second_run_replaces_rather_than_duplicates(db):
    source_user, game, rows = await _build_source_user(db)

    await capture(db, source_user.discord_id)
    sessions_captured, preferences_captured = await capture(db, source_user.discord_id)

    assert sessions_captured == 1
    assert preferences_captured == 1

    seed_sessions = (await db.execute(select(DemoSeedSession))).scalars().all()
    assert len(seed_sessions) == 1

    seed_prefs = (await db.execute(select(DemoSeedPreference))).scalars().all()
    assert len(seed_prefs) == 1


async def test_confirmation_matching_username_captures(db):
    source_user, game, rows = await _build_source_user(db)

    result = await capture_with_confirmation(
        db, source_user.discord_id, confirm=lambda _prompt: source_user.username
    )

    assert result == (1, 1)
    assert len((await db.execute(select(DemoSeedSession))).scalars().all()) == 1


async def test_confirmation_mismatch_captures_nothing(db):
    source_user, game, rows = await _build_source_user(db)

    result = await capture_with_confirmation(
        db, source_user.discord_id, confirm=lambda _prompt: "not-the-username"
    )

    assert result is None
    assert (await db.execute(select(DemoSeedSession))).scalars().all() == []
    assert (await db.execute(select(DemoSeedPreference))).scalars().all() == []


async def test_assume_yes_skips_confirmation(db):
    source_user, game, rows = await _build_source_user(db)

    def _fail_if_called(_prompt):
        raise AssertionError("confirm() must not be called when assume_yes=True")

    result = await capture_with_confirmation(
        db, source_user.discord_id, assume_yes=True, confirm=_fail_if_called
    )

    assert result == (1, 1)


async def test_unknown_discord_id_captures_nothing(db):
    """A mistyped id must abort cleanly rather than truncating the seed
    tables and capturing nothing from a user that doesn't exist."""
    await _build_source_user(db)

    result = await capture_with_confirmation(db, "000000000000000000", assume_yes=True)

    assert result is None
    assert (await db.execute(select(DemoSeedSession))).scalars().all() == []
    assert (await db.execute(select(DemoSeedPreference))).scalars().all() == []
