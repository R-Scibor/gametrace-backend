from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.models.session import SessionSource, SessionStatus
from tests.factories import make_game, make_pref, make_session


def _monday_in_tz(tz_name: str = "UTC") -> date:
    today = datetime.now(ZoneInfo(tz_name)).date()
    return today - timedelta(days=today.weekday())


# ── Shape / defaults ──────────────────────────────────────────────────────────

async def test_weekly_trend_empty_user(authed_client):
    resp = await authed_client.get("/api/v1/stats/weekly-trend")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["weeks"]) == 12
    assert all(w["total_seconds"] == 0 for w in data["weeks"])
    # Last entry is current week's Monday in UTC (default user tz)
    assert data["weeks"][-1]["week_start"] == _monday_in_tz("UTC").isoformat()


async def test_weekly_trend_default_count_is_12(authed_client):
    resp = await authed_client.get("/api/v1/stats/weekly-trend")

    assert resp.status_code == 200
    assert len(resp.json()["weeks"]) == 12


async def test_weekly_trend_custom_weeks_param(authed_client):
    resp = await authed_client.get("/api/v1/stats/weekly-trend?weeks=4")

    assert resp.status_code == 200
    assert len(resp.json()["weeks"]) == 4


async def test_weekly_trend_invalid_weeks_param(authed_client):
    assert (await authed_client.get("/api/v1/stats/weekly-trend?weeks=0")).status_code == 422
    assert (await authed_client.get("/api/v1/stats/weekly-trend?weeks=53")).status_code == 422


# ── Bucketing ─────────────────────────────────────────────────────────────────

async def test_weekly_trend_session_in_current_week(authed_client, db, user):
    game = await make_game(db)
    # Pick a moment that's reliably inside the current local week and not in
    # the future: now() - 1 hour. With UTC user tz that's still this week
    # unless run in the first hour of Monday UTC — acceptable test trade-off.
    start = datetime.now(timezone.utc) - timedelta(hours=1)
    await make_session(
        db, user.discord_id, game.id, start, start + timedelta(seconds=3600)
    )

    resp = await authed_client.get("/api/v1/stats/weekly-trend")

    assert resp.status_code == 200
    weeks = resp.json()["weeks"]
    monday = _monday_in_tz("UTC").isoformat()
    by_week = {w["week_start"]: w["total_seconds"] for w in weeks}
    assert by_week[monday] == 3600
    assert sum(v for k, v in by_week.items() if k != monday) == 0


async def test_weekly_trend_sessions_two_weeks(authed_client, db, user):
    game = await make_game(db)
    now = datetime.now(timezone.utc)
    this_week_start = now - timedelta(hours=1)
    await make_session(
        db, user.discord_id, game.id,
        this_week_start, this_week_start + timedelta(seconds=600),
    )
    # Land on previous-week Wednesday in UTC, so it's unambiguously in the
    # Monday-Sunday week prior to the current one regardless of weekday now.
    monday_now = _monday_in_tz("UTC")
    last_week_wed = datetime.combine(
        monday_now - timedelta(days=5), datetime.min.time(), tzinfo=timezone.utc
    ).replace(hour=12)
    await make_session(
        db, user.discord_id, game.id,
        last_week_wed, last_week_wed + timedelta(seconds=1200),
    )

    resp = await authed_client.get("/api/v1/stats/weekly-trend")

    assert resp.status_code == 200
    weeks = resp.json()["weeks"]
    by_week = {w["week_start"]: w["total_seconds"] for w in weeks}
    monday_prev = monday_now - timedelta(weeks=1)
    assert by_week[monday_now.isoformat()] == 600
    assert by_week[monday_prev.isoformat()] == 1200


# ── Exclusions ────────────────────────────────────────────────────────────────

async def test_weekly_trend_excludes_error_sessions(authed_client, db, user):
    game = await make_game(db)
    start = datetime.now(timezone.utc) - timedelta(hours=1)
    await make_session(
        db, user.discord_id, game.id, start, start + timedelta(seconds=600),
        status=SessionStatus.ERROR,
    )

    resp = await authed_client.get("/api/v1/stats/weekly-trend")

    assert resp.status_code == 200
    assert all(w["total_seconds"] == 0 for w in resp.json()["weeks"])


async def test_weekly_trend_excludes_deleted(authed_client, db, user):
    game = await make_game(db)
    start = datetime.now(timezone.utc) - timedelta(hours=1)
    await make_session(
        db, user.discord_id, game.id, start, start + timedelta(seconds=600),
        deleted_at=datetime.now(timezone.utc),
    )

    resp = await authed_client.get("/api/v1/stats/weekly-trend")

    assert resp.status_code == 200
    assert all(w["total_seconds"] == 0 for w in resp.json()["weeks"])


async def test_weekly_trend_excludes_ignored_game(authed_client, db, user):
    game = await make_game(db)
    await make_pref(db, user.discord_id, game.id, is_ignored=True)
    start = datetime.now(timezone.utc) - timedelta(hours=1)
    await make_session(
        db, user.discord_id, game.id, start, start + timedelta(seconds=600)
    )

    resp = await authed_client.get("/api/v1/stats/weekly-trend")

    assert resp.status_code == 200
    assert all(w["total_seconds"] == 0 for w in resp.json()["weeks"])


# ── ONGOING included ──────────────────────────────────────────────────────────

async def test_weekly_trend_includes_ongoing(authed_client, db, user):
    game = await make_game(db)
    start = datetime.now(timezone.utc) - timedelta(minutes=30)
    await make_session(
        db, user.discord_id, game.id, start,
        end_time=None,
        status=SessionStatus.ONGOING,
        source=SessionSource.BOT,
    )

    resp = await authed_client.get("/api/v1/stats/weekly-trend")

    assert resp.status_code == 200
    weeks = resp.json()["weeks"]
    total = sum(w["total_seconds"] for w in weeks)
    assert 1700 <= total <= 1900


# ── Window cutoff ─────────────────────────────────────────────────────────────

async def test_weekly_trend_old_session_excluded(authed_client, db, user):
    game = await make_game(db)
    old = datetime.now(timezone.utc) - timedelta(weeks=20)
    await make_session(
        db, user.discord_id, game.id, old, old + timedelta(seconds=3600)
    )

    resp = await authed_client.get("/api/v1/stats/weekly-trend?weeks=12")

    assert resp.status_code == 200
    assert all(w["total_seconds"] == 0 for w in resp.json()["weeks"])


# ── Timezone bucketing ────────────────────────────────────────────────────────

async def test_weekly_trend_respects_user_timezone(authed_client, db, user):
    """A session at UTC time that's still Sunday in UTC but Monday local in
    Asia/Tokyo (UTC+9) must be bucketed into the Monday-local week, not the
    previous week."""
    user.timezone = "Asia/Tokyo"
    db.add(user)
    await db.flush()

    tokyo = ZoneInfo("Asia/Tokyo")
    # Build a fixed past Sunday-UTC / Monday-Tokyo timestamp far enough back to
    # be inside the 12-week window but outside the current week noise.
    # Find a recent Sunday at 23:00 UTC that maps to Monday 08:00 Tokyo.
    now_tokyo = datetime.now(tokyo)
    monday_local = now_tokyo.date() - timedelta(days=now_tokyo.weekday())
    # Two weeks ago (Tokyo Monday)
    target_monday_local = monday_local - timedelta(weeks=2)
    # 08:00 local that Monday = 23:00 UTC the previous Sunday.
    local_monday_8am = datetime.combine(
        target_monday_local, datetime.min.time(), tzinfo=tokyo
    ).replace(hour=8)
    start_utc = local_monday_8am.astimezone(timezone.utc)
    # Sanity: in UTC this is Sunday.
    assert start_utc.weekday() == 6  # Sunday

    game = await make_game(db)
    await make_session(
        db, user.discord_id, game.id, start_utc, start_utc + timedelta(seconds=600)
    )

    resp = await authed_client.get("/api/v1/stats/weekly-trend")

    assert resp.status_code == 200
    by_week = {w["week_start"]: w["total_seconds"] for w in resp.json()["weeks"]}
    # Counts toward Monday-local bucket, not the previous week (Sunday-UTC week).
    assert by_week[target_monday_local.isoformat()] == 600
    prev_monday = target_monday_local - timedelta(weeks=1)
    assert by_week[prev_monday.isoformat()] == 0


# ── Ordering ──────────────────────────────────────────────────────────────────

async def test_weekly_trend_oldest_first(authed_client):
    resp = await authed_client.get("/api/v1/stats/weekly-trend")

    assert resp.status_code == 200
    weeks = resp.json()["weeks"]
    dates = [date.fromisoformat(w["week_start"]) for w in weeks]
    assert dates[0] < dates[-1]
    assert dates == sorted(dates)


# ── Auth ──────────────────────────────────────────────────────────────────────

async def test_weekly_trend_unauthorized(client):
    resp = await client.get(
        "/api/v1/stats/weekly-trend", headers={"Authorization": "Bearer badtoken"}
    )

    assert resp.status_code == 401
