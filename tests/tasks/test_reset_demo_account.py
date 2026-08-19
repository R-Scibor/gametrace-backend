"""Nightly demo-account reset — unit tests.

Async tests call _run_demo_reset(db) directly so the rollback fixture keeps
the test DB clean. The sync Celery entry (.run()) isn't exercised — it just
wraps _run_demo_with_engine in asyncio.run, same pattern as the other
cleanup tasks.
"""
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.core.celery_app import celery_app
from app.models.game import UserGamePreference
from app.models.report import Report
from app.models.session import GameSession
from app.models.user import User, UserAuthToken, UserDevice
from app.models.voice_usage import VoiceUsage
from app.services.demo import DEMO_DISCORD_ID, DEMO_LANGUAGE, DEMO_TIMEZONE, DEMO_USERNAME
from app.tasks.cleanup import _run_demo_reset, _run_flicker_purge
from tests.factories import (
    make_demo_seed_preference,
    make_demo_seed_session,
    make_device,
    make_game,
    make_pref,
    make_report,
    make_session,
    make_token,
    make_user,
)


async def _make_demo_user(db, **kwargs):
    return await make_user(db, discord_id=DEMO_DISCORD_ID, username="demo-stale", **kwargs)


async def test_restores_exact_row_counts(db):
    await _make_demo_user(db)
    game = await make_game(db, primary_name="SnapshotGame")

    # Junk that should be wiped before restore.
    await make_session(db, DEMO_DISCORD_ID, game.id, datetime.now(UTC))
    await make_pref(db, DEMO_DISCORD_ID, game.id, is_ignored=True)

    base = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    await make_demo_seed_session(db, game.id, start_time=base, end_time=base + timedelta(hours=1))
    await make_demo_seed_session(
        db, game.id, start_time=base + timedelta(days=1), end_time=base + timedelta(days=1, hours=2)
    )
    await make_demo_seed_preference(db, game.id, is_ignored=True, custom_tag="tag")

    restored = await _run_demo_reset(db)

    assert restored == 2

    sessions = (
        await db.execute(select(GameSession).where(GameSession.user_id == DEMO_DISCORD_ID))
    ).scalars().all()
    assert len(sessions) == 2

    prefs = (
        await db.execute(select(UserGamePreference).where(UserGamePreference.user_id == DEMO_DISCORD_ID))
    ).scalars().all()
    assert len(prefs) == 1
    assert prefs[0].custom_tag == "tag"


async def test_newest_session_lands_on_today_in_demo_timezone(db):
    await _make_demo_user(db)
    game = await make_game(db)

    # Time-of-day is pinned to just after local midnight, and the gap is
    # kept small (30 days, well within one DST regime) rather than years, so
    # the test can't collide with the future-clamp in test_restored_session_
    # never_lands_in_the_future: the shift is a plain timedelta added to a
    # UTC timestamp, so a multi-year gap can itself drift by the DST hour
    # and land in the future — a separate, pre-existing property this test
    # isn't about.
    demo_tz = ZoneInfo(DEMO_TIMEZONE)
    old_base = (datetime.now(demo_tz) - timedelta(days=30)).replace(
        hour=0, minute=0, second=1, microsecond=0
    )
    await make_demo_seed_session(
        db, game.id, start_time=old_base, end_time=old_base + timedelta(minutes=1)
    )

    await _run_demo_reset(db)

    sessions = (
        await db.execute(select(GameSession).where(GameSession.user_id == DEMO_DISCORD_ID))
    ).scalars().all()
    assert len(sessions) == 1

    expected_today = datetime.now(demo_tz).date()
    restored_date = sessions[0].start_time.astimezone(demo_tz).date()
    assert restored_date == expected_today


async def test_relative_spacing_between_sessions_preserved(db):
    await _make_demo_user(db)
    game = await make_game(db)

    base = datetime(2021, 6, 1, 10, 0, tzinfo=UTC)
    earlier = base
    later = base + timedelta(days=3, hours=5)
    await make_demo_seed_session(db, game.id, start_time=earlier, end_time=earlier + timedelta(hours=1))
    await make_demo_seed_session(db, game.id, start_time=later, end_time=later + timedelta(hours=2))

    await _run_demo_reset(db)

    sessions = (
        await db.execute(
            select(GameSession)
            .where(GameSession.user_id == DEMO_DISCORD_ID)
            .order_by(GameSession.start_time)
        )
    ).scalars().all()
    assert len(sessions) == 2

    original_gap = later - earlier
    restored_gap = sessions[1].start_time - sessions[0].start_time
    assert restored_gap == original_gap

    # Time-of-day is preserved too (whole-day delta only).
    assert sessions[0].start_time.astimezone(UTC).time() == earlier.time()


async def test_restored_session_never_lands_in_the_future(db):
    """A whole-day delta preserves time-of-day, so a session captured with a
    time-of-day later than 'now' would land in the future after a naive
    shift onto today. Build exactly that case: an old seed session whose
    time-of-day is 2h ahead of the current moment, so the date-only delta
    would overshoot into the future by up to a day."""
    await _make_demo_user(db)
    game = await make_game(db)

    demo_tz = ZoneInfo(DEMO_TIMEZONE)
    future_moment_today = datetime.now(demo_tz) + timedelta(hours=2)
    seed_start = future_moment_today - timedelta(days=100)
    seed_end = seed_start + timedelta(hours=1)

    await make_demo_seed_session(db, game.id, start_time=seed_start, end_time=seed_end)

    await _run_demo_reset(db)

    sessions = (
        await db.execute(select(GameSession).where(GameSession.user_id == DEMO_DISCORD_ID))
    ).scalars().all()
    assert len(sessions) == 1

    now = datetime.now(UTC)
    assert sessions[0].start_time <= now
    assert sessions[0].end_time <= now

    # The gap between the original start/end time-of-day is untouched —
    # only the whole-day offset moved, never per-row rebasing.
    assert (sessions[0].end_time - sessions[0].start_time) == (seed_end - seed_start)


async def test_clears_pending_deletion_fields(db):
    now = datetime.now(UTC)
    await _make_demo_user(
        db, deletion_requested_at=now - timedelta(days=1), purge_at=now + timedelta(days=6)
    )

    await _run_demo_reset(db)

    demo = (await db.execute(select(User).where(User.discord_id == DEMO_DISCORD_ID))).scalar_one()
    assert demo.deletion_requested_at is None
    assert demo.purge_at is None


async def test_clearing_pending_deletion_writes_cancelled_event(db):
    from app.models.account_deletion_event import AccountDeletionEvent

    now = datetime.now(UTC)
    await _make_demo_user(
        db, deletion_requested_at=now - timedelta(days=1), purge_at=now + timedelta(days=6)
    )

    await _run_demo_reset(db)

    events = (
        await db.execute(
            select(AccountDeletionEvent).where(AccountDeletionEvent.discord_id == DEMO_DISCORD_ID)
        )
    ).scalars().all()
    assert len(events) == 1
    assert events[0].event == "cancelled"


async def test_reset_with_no_pending_deletion_writes_no_event(db):
    from app.models.account_deletion_event import AccountDeletionEvent

    await _make_demo_user(db)

    await _run_demo_reset(db)

    events = (
        await db.execute(
            select(AccountDeletionEvent).where(AccountDeletionEvent.discord_id == DEMO_DISCORD_ID)
        )
    ).scalars().all()
    assert events == []


async def test_recreates_missing_user_row(db):
    # No demo user pre-seeded at all.
    assert (
        await db.execute(select(User).where(User.discord_id == DEMO_DISCORD_ID))
    ).scalar_one_or_none() is None

    await _run_demo_reset(db)

    demo = (await db.execute(select(User).where(User.discord_id == DEMO_DISCORD_ID))).scalar_one()
    assert demo.username == DEMO_USERNAME
    assert demo.timezone == DEMO_TIMEZONE
    assert demo.language == DEMO_LANGUAGE
    assert demo.is_admin is False


async def test_resets_is_admin_and_identity_fields(db):
    await _make_demo_user(
        db, is_admin=True, tz="America/New_York"
    )

    await _run_demo_reset(db)

    demo = (await db.execute(select(User).where(User.discord_id == DEMO_DISCORD_ID))).scalar_one()
    assert demo.is_admin is False
    assert demo.username == DEMO_USERNAME
    assert demo.timezone == DEMO_TIMEZONE
    assert demo.language == DEMO_LANGUAGE
    assert demo.weekly_report_enabled is True
    assert demo.push_enabled is True


async def test_leaves_user_auth_tokens_untouched(db):
    await _make_demo_user(db)
    token = await make_token(db, DEMO_DISCORD_ID)

    await _run_demo_reset(db)

    remaining = (
        await db.execute(select(UserAuthToken).where(UserAuthToken.user_id == DEMO_DISCORD_ID))
    ).scalars().all()
    assert len(remaining) == 1
    assert UserAuthToken.hash_token(token) == remaining[0].token


async def test_deletes_devices_voice_usage_and_reports(db):
    await _make_demo_user(db)
    await make_device(db, DEMO_DISCORD_ID, "demo-device-tok")
    voice = VoiceUsage(user_id=DEMO_DISCORD_ID, game_resolved=True, fields_extracted=2)
    db.add(voice)
    await make_report(db, DEMO_DISCORD_ID, message="demo report")
    await db.flush()

    await _run_demo_reset(db)

    assert (
        await db.execute(select(UserDevice).where(UserDevice.user_id == DEMO_DISCORD_ID))
    ).scalars().all() == []
    assert (
        await db.execute(select(VoiceUsage).where(VoiceUsage.user_id == DEMO_DISCORD_ID))
    ).scalars().all() == []
    assert (
        await db.execute(select(Report).where(Report.user_id == DEMO_DISCORD_ID))
    ).scalars().all() == []


async def test_other_users_data_never_touched(db, user):
    await _make_demo_user(db)
    game = await make_game(db)
    other_session = await make_session(db, user.discord_id, game.id, datetime.now(UTC))
    other_pref = await make_pref(db, user.discord_id, game.id, is_ignored=True)

    await _run_demo_reset(db)

    assert (
        await db.execute(select(GameSession).where(GameSession.id == other_session.id))
    ).scalar_one_or_none() is not None
    assert (
        await db.execute(select(UserGamePreference).where(UserGamePreference.id == other_pref.id))
    ).scalar_one_or_none() is not None


async def test_restore_failure_rolls_back(db, monkeypatch):
    """A crash partway through the restore (e.g. an integrity error at
    commit time) must fail the whole transaction, not leave the demo
    account emptied.

    This proves a *real* rollback rather than passing for a harness reason:
    the prior demo session/is_admin state is committed via db.commit()
    *before* calling _run_demo_reset (a real SAVEPOINT under
    join_transaction_mode="create_savepoint"), then db.commit is monkeypatched
    to raise once _run_demo_reset has staged its deletes + restore inserts.
    _run_demo_reset is expected to catch that, call its own (real,
    unpatched) db.rollback(), and re-raise. The assertions below run on the
    same session immediately afterwards, before the outer per-test rollback
    ever fires, so they only pass if _run_demo_reset's own rollback actually
    undid the delete + partial insert against the real database.
    """
    demo = await _make_demo_user(db, is_admin=True)
    game = await make_game(db, primary_name="PriorGame")
    prior_session = await make_session(db, DEMO_DISCORD_ID, game.id, datetime.now(UTC))
    await db.commit()

    await make_demo_seed_session(db, game_id=game.id, start_time=datetime.now(UTC))
    await db.commit()

    async def _boom():
        raise RuntimeError("simulated crash mid-restore")

    monkeypatch.setattr(db, "commit", _boom)

    raised = False
    try:
        await _run_demo_reset(db)
    except RuntimeError:
        raised = True

    assert raised, "expected _run_demo_reset to propagate the crash"

    # Undo the monkeypatch so the assertions below (and the harness's own
    # rollback-based teardown) use the real commit/rollback machinery.
    monkeypatch.undo()

    surviving_sessions = (
        await db.execute(select(GameSession).where(GameSession.user_id == DEMO_DISCORD_ID))
    ).scalars().all()
    assert len(surviving_sessions) == 1
    assert surviving_sessions[0].id == prior_session.id

    surviving_demo = (
        await db.execute(select(User).where(User.discord_id == DEMO_DISCORD_ID))
    ).scalar_one()
    assert surviving_demo.is_admin is True
    assert surviving_demo.username == demo.username


async def test_duplicate_seed_prefs_for_same_game_do_not_wedge_reset(db):
    """A merge (or any future path) can leave two demo_seed_preferences rows
    pointing at the same game_id. UserGamePreference has UNIQUE(user_id,
    game_id) at the restore destination, so a raw insert-all would violate
    it and roll back the whole reset — permanently, since the duplicate is
    itself part of the frozen snapshot. The restore must dedupe defensively
    regardless of how the duplicate got there."""
    await _make_demo_user(db)
    game = await make_game(db)

    await make_demo_seed_preference(db, game.id, is_ignored=True, custom_tag="first")
    await make_demo_seed_preference(db, game.id, is_ignored=False, custom_tag="second")

    # Must not raise (no IntegrityError / rollback).
    await _run_demo_reset(db)

    prefs = (
        await db.execute(select(UserGamePreference).where(UserGamePreference.user_id == DEMO_DISCORD_ID))
    ).scalars().all()
    assert len(prefs) == 1
    assert prefs[0].game_id == game.id


async def test_counts_stable_after_flicker_purge(db):
    await _make_demo_user(db)
    game = await make_game(db)

    base = datetime.now(UTC) - timedelta(days=10)
    await make_demo_seed_session(db, game.id, start_time=base, end_time=base + timedelta(minutes=30))

    restored = await _run_demo_reset(db)
    assert restored == 1

    await _run_flicker_purge(db)

    sessions = (
        await db.execute(select(GameSession).where(GameSession.user_id == DEMO_DISCORD_ID))
    ).scalars().all()
    assert len(sessions) == 1


def test_beat_schedule_has_reset_demo_account():
    sched = celery_app.conf.beat_schedule
    assert "reset_demo_account" in sched
    entry = sched["reset_demo_account"]
    assert entry["task"] == "tasks.reset_demo_account"
    schedule = entry["schedule"]
    assert schedule.hour == {3}
    assert schedule.minute == {0}


async def test_reset_survives_a_real_user_holding_the_demo_username(db):
    """users.username is no longer unique, so a real account can hold the demo
    name. The reset must still write it rather than fail on a constraint."""
    await _make_demo_user(db)
    await make_user(db, discord_id="999999999999999999", username=DEMO_USERNAME)

    await _run_demo_reset(db)

    demo = await db.get(User, DEMO_DISCORD_ID)
    assert demo.username == DEMO_USERNAME
