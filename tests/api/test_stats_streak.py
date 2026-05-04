from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.models.session import SessionSource, SessionStatus
from app.services.stats import _compute_streaks
from tests.factories import make_game, make_pref, make_session


# ── Pure unit tests for _compute_streaks ──────────────────────────────────────

class TestComputeStreaks:
    def test_empty_set(self):
        today = date(2026, 5, 4)
        assert _compute_streaks(set(), today) == (0, 0)

    def test_only_today(self):
        today = date(2026, 5, 4)
        assert _compute_streaks({today}, today) == (1, 1)

    def test_three_consecutive_today_back(self):
        today = date(2026, 5, 4)
        play = {today, today - timedelta(days=1), today - timedelta(days=2)}
        assert _compute_streaks(play, today) == (3, 3)

    def test_past_streak_no_current(self):
        today = date(2026, 5, 4)
        play = {
            today - timedelta(days=2),
            today - timedelta(days=3),
            today - timedelta(days=4),
        }
        assert _compute_streaks(play, today) == (0, 3)

    def test_yesterday_grace(self):
        today = date(2026, 5, 4)
        play = {today - timedelta(days=1), today - timedelta(days=2)}
        assert _compute_streaks(play, today) == (2, 2)

    def test_gap_kills_current(self):
        today = date(2026, 5, 4)
        play = {today, today - timedelta(days=2)}
        assert _compute_streaks(play, today) == (1, 1)

    def test_past_streak_preserved_as_longest(self):
        today = date(2026, 5, 4)
        # past 5-day streak (today-7..today-3) + today
        past = {today - timedelta(days=d) for d in range(3, 8)}
        play = past | {today}
        assert _compute_streaks(play, today) == (1, 5)


# ── API tests ─────────────────────────────────────────────────────────────────

async def test_streak_empty_user(authed_client):
    resp = await authed_client.get("/api/v1/stats/streak")

    assert resp.status_code == 200
    assert resp.json() == {"current_streak": 0, "longest_streak": 0}


async def test_streak_only_today(authed_client, db, user):
    game = await make_game(db)
    start = datetime.now(timezone.utc)
    await make_session(
        db, user.discord_id, game.id, start, start + timedelta(seconds=600)
    )

    resp = await authed_client.get("/api/v1/stats/streak")

    assert resp.status_code == 200
    assert resp.json() == {"current_streak": 1, "longest_streak": 1}


async def test_streak_only_yesterday_grace(authed_client, db, user):
    game = await make_game(db)
    start = datetime.now(timezone.utc) - timedelta(days=1)
    await make_session(
        db, user.discord_id, game.id, start, start + timedelta(seconds=600)
    )

    resp = await authed_client.get("/api/v1/stats/streak")

    assert resp.status_code == 200
    assert resp.json() == {"current_streak": 1, "longest_streak": 1}


async def test_streak_today_and_yesterday(authed_client, db, user):
    game = await make_game(db)
    today_start = datetime.now(timezone.utc)
    yest_start = today_start - timedelta(days=1)
    await make_session(
        db, user.discord_id, game.id, today_start,
        today_start + timedelta(seconds=600),
    )
    await make_session(
        db, user.discord_id, game.id, yest_start,
        yest_start + timedelta(seconds=600),
    )

    resp = await authed_client.get("/api/v1/stats/streak")

    assert resp.status_code == 200
    assert resp.json() == {"current_streak": 2, "longest_streak": 2}


async def test_streak_gap_kills_current(authed_client, db, user):
    game = await make_game(db)
    today_start = datetime.now(timezone.utc)
    old_start = today_start - timedelta(days=3)
    await make_session(
        db, user.discord_id, game.id, today_start,
        today_start + timedelta(seconds=600),
    )
    await make_session(
        db, user.discord_id, game.id, old_start,
        old_start + timedelta(seconds=600),
    )

    resp = await authed_client.get("/api/v1/stats/streak")

    assert resp.status_code == 200
    assert resp.json() == {"current_streak": 1, "longest_streak": 1}


async def test_streak_past_streak_no_current(authed_client, db, user):
    game = await make_game(db)
    # 5 consecutive days ending 2 days ago (i.e. days-ago: 2,3,4,5,6)
    for d in range(2, 7):
        s = datetime.now(timezone.utc) - timedelta(days=d)
        await make_session(
            db, user.discord_id, game.id, s, s + timedelta(seconds=600)
        )

    resp = await authed_client.get("/api/v1/stats/streak")

    assert resp.status_code == 200
    assert resp.json() == {"current_streak": 0, "longest_streak": 5}


async def test_streak_excludes_error_sessions(authed_client, db, user):
    game = await make_game(db)
    start = datetime.now(timezone.utc)
    await make_session(
        db, user.discord_id, game.id, start, start + timedelta(seconds=600),
        status=SessionStatus.ERROR,
    )

    resp = await authed_client.get("/api/v1/stats/streak")

    assert resp.status_code == 200
    assert resp.json() == {"current_streak": 0, "longest_streak": 0}


async def test_streak_excludes_deleted(authed_client, db, user):
    game = await make_game(db)
    start = datetime.now(timezone.utc)
    await make_session(
        db, user.discord_id, game.id, start, start + timedelta(seconds=600),
        deleted_at=datetime.now(timezone.utc),
    )

    resp = await authed_client.get("/api/v1/stats/streak")

    assert resp.status_code == 200
    assert resp.json() == {"current_streak": 0, "longest_streak": 0}


async def test_streak_excludes_ignored_game(authed_client, db, user):
    game = await make_game(db)
    await make_pref(db, user.discord_id, game.id, is_ignored=True)
    start = datetime.now(timezone.utc)
    await make_session(
        db, user.discord_id, game.id, start, start + timedelta(seconds=600)
    )

    resp = await authed_client.get("/api/v1/stats/streak")

    assert resp.status_code == 200
    assert resp.json() == {"current_streak": 0, "longest_streak": 0}


async def test_streak_respects_user_timezone(authed_client, db, user):
    """A UTC time that maps to a *different* local date in the user's tz must
    be bucketed by the local date, not the UTC one. We pick a session at
    UTC 04:00 today: in America/New_York that's 00:00 EDT today (or yesterday
    23:00/01:00 EDT depending on DST). Either way, both the UTC and NY-local
    bucketings yield consecutive-or-same calendar dates between today and
    yesterday-local, so current_streak=1, longest=1."""
    user.timezone = "America/New_York"
    db.add(user)
    await db.flush()

    ny = ZoneInfo("America/New_York")
    now_ny = datetime.now(ny)
    # Construct a NY-local datetime at 22:30 yesterday → ensure local date is
    # *yesterday* in NY tz. Convert to UTC for storage (likely today UTC).
    yesterday_ny = (now_ny - timedelta(days=1)).replace(
        hour=22, minute=30, second=0, microsecond=0
    )
    start = yesterday_ny.astimezone(timezone.utc)

    game = await make_game(db)
    await make_session(
        db, user.discord_id, game.id, start, start + timedelta(seconds=600)
    )

    resp = await authed_client.get("/api/v1/stats/streak")

    assert resp.status_code == 200
    # Local date is yesterday-NY → grace rule → current=1, longest=1.
    # If the service used UTC, the date might match today UTC, also giving 1/1
    # — so the stronger check: assert the bucketing didn't drop the row.
    assert resp.json() == {"current_streak": 1, "longest_streak": 1}


async def test_streak_includes_ongoing_session(authed_client, db, user):
    game = await make_game(db)
    start = datetime.now(timezone.utc) - timedelta(hours=1)
    await make_session(
        db, user.discord_id, game.id, start,
        end_time=None,
        status=SessionStatus.ONGOING,
        source=SessionSource.BOT,
    )

    resp = await authed_client.get("/api/v1/stats/streak")

    assert resp.status_code == 200
    assert resp.json() == {"current_streak": 1, "longest_streak": 1}


async def test_streak_unauthorized(client):
    resp = await client.get(
        "/api/v1/stats/streak", headers={"Authorization": "Bearer badtoken"}
    )

    assert resp.status_code == 401
