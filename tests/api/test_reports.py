from sqlalchemy import select

from app.models.report import Report

VALID_CONTEXT = {
    "screen": "Dashboard",
    "platform": "android",
    "osVersion": "14",
    "appVersion": "1.2.3",
}


async def test_create_report_persists_row(authed_client, db, user):
    resp = await authed_client.post(
        "/api/v1/reports",
        json={"message": "  something is broken  ", "context": VALID_CONTEXT},
    )

    assert resp.status_code == 201
    body = resp.json()
    assert isinstance(body["id"], int)
    assert "created_at" in body

    row = (
        await db.execute(select(Report).where(Report.id == body["id"]))
    ).scalar_one()
    assert row.user_id == user.discord_id
    assert row.message == "something is broken"  # trimmed
    assert row.context == VALID_CONTEXT  # camelCase preserved


async def test_create_report_accepts_numeric_os_version(authed_client, db):
    ctx = {**VALID_CONTEXT, "osVersion": 14}
    resp = await authed_client.post(
        "/api/v1/reports",
        json={"message": "numbers ok", "context": ctx},
    )

    assert resp.status_code == 201
    row = (
        await db.execute(select(Report).where(Report.id == resp.json()["id"]))
    ).scalar_one()
    assert row.context["osVersion"] == 14


async def test_create_report_rejects_blank_message(authed_client):
    resp = await authed_client.post(
        "/api/v1/reports",
        json={"message": "   ", "context": VALID_CONTEXT},
    )
    assert resp.status_code == 422


async def test_create_report_rejects_overlong_message(authed_client):
    resp = await authed_client.post(
        "/api/v1/reports",
        json={"message": "x" * 4001, "context": VALID_CONTEXT},
    )
    assert resp.status_code == 422


async def test_create_report_rejects_missing_context_field(authed_client):
    bad_context = {"screen": "Dashboard", "platform": "android"}  # no os/app version
    resp = await authed_client.post(
        "/api/v1/reports",
        json={"message": "incomplete", "context": bad_context},
    )
    assert resp.status_code == 422


async def test_create_report_requires_auth(client):
    resp = await client.post(
        "/api/v1/reports",
        json={"message": "no token", "context": VALID_CONTEXT},
    )
    assert resp.status_code == 403
