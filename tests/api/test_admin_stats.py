from app.models.game import EnrichmentStatus
from app.models.report import Report
from app.models.session import SessionStatus

from tests.factories import dt, make_game, make_session, make_user

URL = "/api/v1/admin/stats/overview"


# ── Auth gate ────────────────────────────────────────────────────────────────

async def test_unauthenticated_returns_401(client, db):
    resp = await client.get(URL, headers={"Authorization": "Bearer badtoken"})
    assert resp.status_code == 401


async def test_non_admin_returns_403(authed_client, db, user):
    resp = await authed_client.get(URL)
    assert resp.status_code == 403


# ── Happy path ───────────────────────────────────────────────────────────────

async def test_overview_aggregates(admin_client, db, admin_user):
    # A second (non-admin) user so user_count reflects >1 account.
    player = await make_user(db, discord_id="333333333333333333", username="player")

    # Games across enrichment statuses.
    enriched = await make_game(db, "Enriched", enrichment_status=EnrichmentStatus.ENRICHED)
    review = await make_game(db, "Review", enrichment_status=EnrichmentStatus.NEEDS_REVIEW)
    pending = await make_game(db, "Pending", enrichment_status=EnrichmentStatus.PENDING)

    # COMPLETED visible session — 3600s, counts toward total_seconds + session_count.
    await make_session(
        db, player.discord_id, enriched.id,
        start_time=dt(hours_ago=2), end_time=dt(hours_ago=1),
        status=SessionStatus.COMPLETED,
    )
    # ONGOING — counts in session_count, NOT total_seconds.
    await make_session(
        db, player.discord_id, enriched.id,
        start_time=dt(hours_ago=1), end_time=None, status=SessionStatus.ONGOING,
    )
    # ERROR — counts in session_count, NOT total_seconds.
    await make_session(
        db, player.discord_id, review.id,
        start_time=dt(hours_ago=5), end_time=dt(hours_ago=4), status=SessionStatus.ERROR,
    )
    # Soft-deleted COMPLETED — excluded from BOTH counts.
    await make_session(
        db, player.discord_id, enriched.id,
        start_time=dt(hours_ago=8), end_time=dt(hours_ago=7),
        status=SessionStatus.COMPLETED, deleted_at=dt(hours_ago=1),
    )

    db.add(Report(user_id=player.discord_id, message="bug", context={"screen": "Home"}))
    db.add(
        Report(
            user_id=player.discord_id,
            message="already handled",
            context={"screen": "Home"},
            status="closed",
        )
    )
    await db.flush()

    resp = await admin_client.get(URL)
    assert resp.status_code == 200
    body = resp.json()

    assert body["user_count"] == 2                 # admin_user + player
    assert body["session_count"] == 3              # COMPLETED + ONGOING + ERROR (soft-deleted excluded)
    assert body["total_seconds"] == 3600           # only the COMPLETED visible session
    assert body["game_count"] == 3
    assert body["needs_review_count"] == 1
    assert body["pending_enrichment_count"] == 1
    assert body["open_reports_count"] == 1


async def test_overview_empty_db_all_zero(admin_client, db, admin_user):
    resp = await admin_client.get(URL)
    assert resp.status_code == 200
    body = resp.json()
    assert body["session_count"] == 0
    assert body["total_seconds"] == 0
    assert body["game_count"] == 0
    assert body["open_reports_count"] == 0
    assert body["user_count"] == 1                 # the admin_user fixture only
