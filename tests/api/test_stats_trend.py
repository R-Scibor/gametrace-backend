from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.models.session import SessionSource, SessionStatus
from tests.factories import make_game, make_pref, make_session


def _today_in_tz(tz_name: str = "UTC") -> date:
    return datetime.now(ZoneInfo(tz_name)).date()


def _local_midnight_utc(tz_name: str = "UTC") -> datetime:
    """Most recent local midnight in tz_name, as an aware UTC datetime."""
    tz = ZoneInfo(tz_name)
    midnight_local = datetime.combine(
        datetime.now(tz).date(), datetime.min.time(), tzinfo=tz
    )
    return midnight_local.astimezone(timezone.utc)


# ── Granularity mapping ───────────────────────────────────────────────────────

async def test_trend_granularity_daily_for_7(authed_client):
    resp = await authed_client.get("/api/v1/stats/trend?days=7")
    assert resp.status_code == 200
    assert resp.json()["granularity"] == "day"


async def test_trend_granularity_daily_for_30(authed_client):
    resp = await authed_client.get("/api/v1/stats/trend?days=30")
    assert resp.status_code == 200
    assert resp.json()["granularity"] == "day"


async def test_trend_granularity_weekly_for_90(authed_client):
    resp = await authed_client.get("/api/v1/stats/trend?days=90")
    assert resp.status_code == 200
    assert resp.json()["granularity"] == "week"


async def test_trend_granularity_monthly_for_all_time(authed_client):
    resp = await authed_client.get("/api/v1/stats/trend?days=0")
    assert resp.status_code == 200
    assert resp.json()["granularity"] == "month"


# ── Shape / defaults / zero-fill ──────────────────────────────────────────────

async def test_trend_default_days_is_7(authed_client):
    resp = await authed_client.get("/api/v1/stats/trend")
    assert resp.status_code == 200
    assert resp.json()["granularity"] == "day"


async def test_trend_empty_user_all_zero(authed_client):
    resp = await authed_client.get("/api/v1/stats/trend?days=30")
    assert resp.status_code == 200
    buckets = resp.json()["buckets"]
    assert all(b["total_seconds"] == 0 for b in buckets)
    # Last daily bucket is today (default UTC tz).
    assert buckets[-1]["bucket_start"] == _today_in_tz("UTC").isoformat()


async def test_trend_buckets_contiguous_and_chronological(authed_client):
    resp = await authed_client.get("/api/v1/stats/trend?days=30")
    assert resp.status_code == 200
    starts = [date.fromisoformat(b["bucket_start"]) for b in resp.json()["buckets"]]
    assert starts == sorted(starts)
    # Daily granularity → every adjacent pair is exactly one day apart (no gaps).
    assert all(b - a == timedelta(days=1) for a, b in zip(starts, starts[1:]))


# ── Daily bucketing ───────────────────────────────────────────────────────────

async def test_trend_daily_session_today(authed_client, db, user):
    game = await make_game(db)
    start = datetime.now(timezone.utc) - timedelta(hours=1)
    await make_session(
        db, user.discord_id, game.id, start, start + timedelta(seconds=3600)
    )
    resp = await authed_client.get("/api/v1/stats/trend?days=30")
    assert resp.status_code == 200
    by_bucket = {b["bucket_start"]: b["total_seconds"] for b in resp.json()["buckets"]}
    today = _today_in_tz("UTC").isoformat()
    assert by_bucket[today] == 3600
    assert sum(v for k, v in by_bucket.items() if k != today) == 0


async def test_trend_daily_splits_across_midnight(authed_client, db, user):
    game = await make_game(db)
    # Start one hour before local midnight → 1h yesterday, 1h today.
    start = _local_midnight_utc("UTC") - timedelta(hours=1)
    await make_session(
        db, user.discord_id, game.id, start, start + timedelta(seconds=2 * 3600)
    )
    resp = await authed_client.get("/api/v1/stats/trend?days=30")
    assert resp.status_code == 200
    by_bucket = {b["bucket_start"]: b["total_seconds"] for b in resp.json()["buckets"]}
    today = _today_in_tz("UTC")
    yesterday = today - timedelta(days=1)
    assert by_bucket[today.isoformat()] == 3600
    assert by_bucket[yesterday.isoformat()] == 3600


# ── Weekly bucketing ──────────────────────────────────────────────────────────

async def test_trend_weekly_bucket_start_is_monday(authed_client):
    resp = await authed_client.get("/api/v1/stats/trend?days=90")
    assert resp.status_code == 200
    starts = [date.fromisoformat(b["bucket_start"]) for b in resp.json()["buckets"]]
    assert all(d.weekday() == 0 for d in starts)  # all Mondays
    assert all(b - a == timedelta(days=7) for a, b in zip(starts, starts[1:]))


# ── Monthly bucketing (all-time) ──────────────────────────────────────────────

async def test_trend_monthly_bucket_start_is_first(authed_client, db, user):
    game = await make_game(db)
    start = datetime.now(timezone.utc) - timedelta(hours=1)
    await make_session(
        db, user.discord_id, game.id, start, start + timedelta(seconds=1800)
    )
    resp = await authed_client.get("/api/v1/stats/trend?days=0")
    assert resp.status_code == 200
    buckets = resp.json()["buckets"]
    starts = [date.fromisoformat(b["bucket_start"]) for b in buckets]
    assert all(d.day == 1 for d in starts)  # first of each month
    this_month = _today_in_tz("UTC").replace(day=1).isoformat()
    by_bucket = {b["bucket_start"]: b["total_seconds"] for b in buckets}
    assert by_bucket[this_month] == 1800


# ── Exclusions: COMPLETED only ────────────────────────────────────────────────

async def test_trend_excludes_ongoing(authed_client, db, user):
    game = await make_game(db)
    start = datetime.now(timezone.utc) - timedelta(minutes=30)
    await make_session(
        db, user.discord_id, game.id, start,
        end_time=None, status=SessionStatus.ONGOING, source=SessionSource.BOT,
    )
    resp = await authed_client.get("/api/v1/stats/trend?days=30")
    assert resp.status_code == 200
    assert all(b["total_seconds"] == 0 for b in resp.json()["buckets"])


async def test_trend_excludes_error(authed_client, db, user):
    game = await make_game(db)
    start = datetime.now(timezone.utc) - timedelta(hours=1)
    await make_session(
        db, user.discord_id, game.id, start, start + timedelta(seconds=600),
        status=SessionStatus.ERROR,
    )
    resp = await authed_client.get("/api/v1/stats/trend?days=30")
    assert resp.status_code == 200
    assert all(b["total_seconds"] == 0 for b in resp.json()["buckets"])


async def test_trend_excludes_deleted(authed_client, db, user):
    game = await make_game(db)
    start = datetime.now(timezone.utc) - timedelta(hours=1)
    await make_session(
        db, user.discord_id, game.id, start, start + timedelta(seconds=600),
        deleted_at=datetime.now(timezone.utc),
    )
    resp = await authed_client.get("/api/v1/stats/trend?days=30")
    assert resp.status_code == 200
    assert all(b["total_seconds"] == 0 for b in resp.json()["buckets"])


async def test_trend_excludes_ignored_game(authed_client, db, user):
    game = await make_game(db)
    await make_pref(db, user.discord_id, game.id, is_ignored=True)
    start = datetime.now(timezone.utc) - timedelta(hours=1)
    await make_session(
        db, user.discord_id, game.id, start, start + timedelta(seconds=600)
    )
    resp = await authed_client.get("/api/v1/stats/trend?days=30")
    assert resp.status_code == 200
    assert all(b["total_seconds"] == 0 for b in resp.json()["buckets"])


# ── Window cutoff ─────────────────────────────────────────────────────────────

async def test_trend_old_session_excluded(authed_client, db, user):
    game = await make_game(db)
    old = datetime.now(timezone.utc) - timedelta(days=60)
    await make_session(
        db, user.discord_id, game.id, old, old + timedelta(seconds=3600)
    )
    resp = await authed_client.get("/api/v1/stats/trend?days=30")
    assert resp.status_code == 200
    assert all(b["total_seconds"] == 0 for b in resp.json()["buckets"])


# ── Timezone bucketing ────────────────────────────────────────────────────────

async def test_trend_respects_user_timezone(authed_client, db, user):
    """A timestamp that is still 'yesterday' in UTC but 'today' in Asia/Tokyo
    must land in the local-today bucket."""
    user.timezone = "Asia/Tokyo"
    db.add(user)
    await db.flush()

    tokyo = ZoneInfo("Asia/Tokyo")
    today_tokyo = datetime.now(tokyo).date()
    # 01:00 Tokyo today = 16:00 UTC yesterday.
    local_1am = datetime.combine(
        today_tokyo, datetime.min.time(), tzinfo=tokyo
    ).replace(hour=1)
    start_utc = local_1am.astimezone(timezone.utc)

    game = await make_game(db)
    await make_session(
        db, user.discord_id, game.id, start_utc, start_utc + timedelta(seconds=600)
    )
    resp = await authed_client.get("/api/v1/stats/trend?days=30")
    assert resp.status_code == 200
    by_bucket = {b["bucket_start"]: b["total_seconds"] for b in resp.json()["buckets"]}
    assert by_bucket[today_tokyo.isoformat()] == 600


# ── Validation / auth ─────────────────────────────────────────────────────────

async def test_trend_invalid_days_param(authed_client):
    assert (await authed_client.get("/api/v1/stats/trend?days=-1")).status_code == 422
    assert (await authed_client.get("/api/v1/stats/trend?days=366")).status_code == 422


async def test_trend_unauthorized(client):
    resp = await client.get(
        "/api/v1/stats/trend", headers={"Authorization": "Bearer badtoken"}
    )
    assert resp.status_code == 401
