from datetime import UTC, datetime, timedelta

from app.models.session import SessionStatus
from tests.factories import dt, make_game, make_session, make_user


async def test_trash_lists_only_trashed_sessions(authed_client, db, user):
    game = await make_game(db)
    # Live (should not appear)
    await make_session(db, user.discord_id, game.id, dt(hours_ago=3), dt(hours_ago=1))
    # Trashed
    trashed = await make_session(
        db, user.discord_id, game.id,
        dt(hours_ago=10), dt(hours_ago=8),
        deleted_at=datetime.now(UTC),
    )

    resp = await authed_client.get("/api/v1/sessions/trash")
    assert resp.status_code == 200
    ids = [s["id"] for s in resp.json()]
    assert ids == [trashed.id]


async def test_trash_includes_purges_at_field(authed_client, db, user):
    game = await make_game(db)
    deleted_at = datetime.now(UTC) - timedelta(days=2)
    await make_session(
        db, user.discord_id, game.id,
        dt(hours_ago=10), dt(hours_ago=8),
        deleted_at=deleted_at,
    )

    resp = await authed_client.get("/api/v1/sessions/trash")
    assert resp.status_code == 200
    row = resp.json()[0]
    purges_at = datetime.fromisoformat(row["purges_at"])
    expected = deleted_at + timedelta(days=7)
    assert abs((purges_at - expected).total_seconds()) < 1


async def test_trash_ordered_by_deleted_at_desc(authed_client, db, user):
    game = await make_game(db)
    older = await make_session(
        db, user.discord_id, game.id, dt(hours_ago=20), dt(hours_ago=19),
        deleted_at=datetime.now(UTC) - timedelta(days=3),
    )
    newer = await make_session(
        db, user.discord_id, game.id, dt(hours_ago=15), dt(hours_ago=14),
        deleted_at=datetime.now(UTC) - timedelta(hours=1),
    )

    resp = await authed_client.get("/api/v1/sessions/trash")
    assert resp.status_code == 200
    ids = [s["id"] for s in resp.json()]
    assert ids == [newer.id, older.id]


async def test_trash_excludes_other_users_sessions(authed_client, db, user):
    game = await make_game(db)
    other = await make_user(db, discord_id="555555555555555555", username="other4")
    await make_session(
        db, other.discord_id, game.id,
        dt(hours_ago=10), dt(hours_ago=8),
        deleted_at=datetime.now(UTC),
    )

    resp = await authed_client.get("/api/v1/sessions/trash")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_trash_pagination(authed_client, db, user):
    game = await make_game(db)
    for i in range(5):
        await make_session(
            db, user.discord_id, game.id,
            dt(hours_ago=20 + i), dt(hours_ago=19 + i),
            deleted_at=datetime.now(UTC) - timedelta(hours=i),
        )

    resp = await authed_client.get("/api/v1/sessions/trash?skip=2&limit=2")
    assert resp.status_code == 200
    assert len(resp.json()) == 2


async def test_trash_rejects_limit_above_100(authed_client):
    resp = await authed_client.get("/api/v1/sessions/trash?limit=101")
    assert resp.status_code == 422


async def test_trash_returns_both_error_and_completed(authed_client, db, user):
    game = await make_game(db)
    completed = await make_session(
        db, user.discord_id, game.id,
        dt(hours_ago=10), dt(hours_ago=8),
        deleted_at=datetime.now(UTC) - timedelta(hours=2),
    )
    error = await make_session(
        db, user.discord_id, game.id,
        dt(hours_ago=15),
        status=SessionStatus.ERROR,
        deleted_at=datetime.now(UTC) - timedelta(hours=1),
    )

    resp = await authed_client.get("/api/v1/sessions/trash")
    ids = {s["id"] for s in resp.json()}
    assert ids == {completed.id, error.id}
