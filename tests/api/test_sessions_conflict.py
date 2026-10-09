"""Overlap 409 stays nested, and OpenAPI describes that envelope."""

from app.main import app
from app.models.session import SessionSource, SessionStatus
from tests.factories import dt, make_game, make_session

_CONFLICT_ROUTES = (
    ("/api/v1/sessions", "post"),
    ("/api/v1/sessions/{session_id}", "patch"),
    ("/api/v1/sessions/{session_id}", "delete"),
    ("/api/v1/sessions/{session_id}/restore", "post"),
)


def test_session_conflict_openapi_is_a_nested_envelope():
    schema = app.openapi()
    schemas = schema["components"]["schemas"]
    envelope = schemas["ConflictEnvelope"]
    assert "conflicting_session" not in envelope["properties"]
    assert envelope["properties"]["detail"]["$ref"].endswith("/ConflictResponse")
    inner = schemas["ConflictResponse"]["properties"]
    assert "detail" in inner
    assert "conflicting_session" in inner

    for path, method in _CONFLICT_ROUTES:
        response = schema["paths"][path][method]["responses"]["409"]
        ref = response["content"]["application/json"]["schema"]["$ref"]
        assert ref.endswith("/ConflictEnvelope")


async def test_create_overlap_409_body_is_nested(authed_client, db, user):
    game = await make_game(db)
    existing = await make_session(
        db, user.discord_id, game.id, dt(hours_ago=3), dt(hours_ago=1),
    )

    resp = await authed_client.post(
        "/api/v1/sessions",
        json={
            "game_id": game.id,
            "start_time": dt(hours_ago=2).isoformat(),
            "end_time": dt(hours_ago=0.5).isoformat(),
        },
    )

    assert resp.status_code == 409
    body = resp.json()
    assert body["detail"]["detail"] == "Session overlaps with an existing session"
    assert body["detail"]["conflicting_session"]["id"] == existing.id
    assert "conflicting_session" not in body


async def test_restore_overlap_409_body_is_nested(authed_client, db, user):
    game = await make_game(db)
    live = await make_session(
        db, user.discord_id, game.id, dt(hours_ago=5), dt(hours_ago=2),
    )
    trashed = await make_session(
        db, user.discord_id, game.id, dt(hours_ago=4), dt(hours_ago=3),
        deleted_at=dt(hours_ago=0.1),
        status=SessionStatus.COMPLETED,
        source=SessionSource.MANUAL,
    )

    resp = await authed_client.post(f"/api/v1/sessions/{trashed.id}/restore")

    assert resp.status_code == 409
    body = resp.json()
    assert body["detail"]["detail"] == "Session overlaps with an existing session"
    assert body["detail"]["conflicting_session"]["id"] == live.id
