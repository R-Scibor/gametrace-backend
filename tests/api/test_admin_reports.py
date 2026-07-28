from tests.factories import dt, make_report, make_user

URL = "/api/v1/admin/reports"


# ── Auth gate ────────────────────────────────────────────────────────────────

async def test_unauthenticated_returns_401(client, db):
    resp = await client.get(URL, headers={"Authorization": "Bearer badtoken"})
    assert resp.status_code == 401


async def test_non_admin_returns_403(authed_client, db, user):
    resp = await authed_client.get(URL)
    assert resp.status_code == 403


# ── Happy path ───────────────────────────────────────────────────────────────

async def test_list_returns_total_and_items_newest_first_with_username(
    admin_client, db, admin_user
):
    player = await make_user(db, discord_id="333333333333333333", username="player")
    older = await make_report(
        db, player.discord_id, message="older", created_at=dt(hours_ago=5)
    )
    newer = await make_report(
        db, player.discord_id, message="newer", created_at=dt(hours_ago=1)
    )

    resp = await admin_client.get(URL)
    assert resp.status_code == 200
    body = resp.json()

    assert body["total"] == 2
    assert [item["id"] for item in body["items"]] == [newer.id, older.id]
    assert body["items"][0]["username"] == "player"
    assert body["items"][0]["message"] == "newer"
    assert body["items"][0]["context"] == {
        "screen": "Home",
        "platform": "android",
        "osVersion": "14",
        "appVersion": "1.0.0",
    }
    assert body["items"][0]["status"] == "open"
    assert body["items"][0]["user_id"] == player.discord_id


async def test_admin_note_surfaces_on_list_when_set_and_null_when_unset(
    admin_client, db, admin_user
):
    player = await make_user(db, discord_id="333333333333333333", username="player")
    noted = await make_report(
        db,
        player.discord_id,
        message="noted",
        admin_note="looked into it",
        created_at=dt(hours_ago=2),
    )
    unnoted = await make_report(
        db, player.discord_id, message="unnoted", created_at=dt(hours_ago=1)
    )

    resp = await admin_client.get(URL)
    assert resp.status_code == 200
    items = {item["id"]: item for item in resp.json()["items"]}

    assert items[noted.id]["admin_note"] == "looked into it"
    assert items[unnoted.id]["admin_note"] is None


async def test_status_filter_narrows_items_and_total(admin_client, db, admin_user):
    player = await make_user(db, discord_id="333333333333333333", username="player")
    await make_report(db, player.discord_id, status="open", created_at=dt(hours_ago=3))
    await make_report(db, player.discord_id, status="triaged", created_at=dt(hours_ago=2))
    await make_report(db, player.discord_id, status="closed", created_at=dt(hours_ago=1))

    resp = await admin_client.get(URL, params={"status": "open"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert len(body["items"]) == 1
    assert body["items"][0]["status"] == "open"


async def test_skip_and_limit_paginate(admin_client, db, admin_user):
    player = await make_user(db, discord_id="333333333333333333", username="player")
    for i in range(5):
        await make_report(
            db, player.discord_id, message=f"report-{i}", created_at=dt(hours_ago=i)
        )

    resp = await admin_client.get(URL, params={"skip": 1, "limit": 2})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 5
    assert len(body["items"]) == 2
    # Newest-first ordering, skip 1: report-0 is newest, so page starts at report-1
    assert body["items"][0]["message"] == "report-1"
    assert body["items"][1]["message"] == "report-2"


async def test_equal_timestamps_order_deterministically_by_id(
    admin_client, db, admin_user
):
    # Same created_at for every row: without a tiebreaker, paging could skip or
    # duplicate rows. The secondary sort (id DESC) must make ordering stable.
    player = await make_user(db, discord_id="333333333333333333", username="player")
    same = dt(hours_ago=1)
    reports = [
        await make_report(db, player.discord_id, message=f"r-{i}", created_at=same)
        for i in range(5)
    ]

    resp = await admin_client.get(URL, params={"limit": 100})
    assert resp.status_code == 200
    ids = [item["id"] for item in resp.json()["items"]]
    assert ids == sorted((r.id for r in reports), reverse=True)


async def test_invalid_status_returns_422(admin_client, db, admin_user):
    resp = await admin_client.get(URL, params={"status": "bogus"})
    assert resp.status_code == 422


# ── PATCH /admin/reports/{id} ──────────────────────────────────────────────────

async def test_patch_unauthenticated_returns_401(client, db):
    resp = await client.patch(
        f"{URL}/1", json={"status": "triaged"}, headers={"Authorization": "Bearer badtoken"}
    )
    assert resp.status_code == 401


async def test_patch_non_admin_returns_403(authed_client, db, user):
    player = await make_user(db, discord_id="333333333333333333", username="player")
    report = await make_report(db, player.discord_id)

    resp = await authed_client.patch(f"{URL}/{report.id}", json={"status": "triaged"})
    assert resp.status_code == 403


async def test_patch_open_to_triaged_returns_updated_item(admin_client, db, admin_user):
    player = await make_user(db, discord_id="333333333333333333", username="player")
    report = await make_report(db, player.discord_id, status="open")

    resp = await admin_client.patch(f"{URL}/{report.id}", json={"status": "triaged"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == report.id
    assert body["status"] == "triaged"
    assert body["username"] == "player"
    assert body["user_id"] == player.discord_id

    # Assert persisted: re-GET the list and confirm the status stuck.
    list_resp = await admin_client.get(URL)
    assert list_resp.status_code == 200
    items = list_resp.json()["items"]
    patched = next(item for item in items if item["id"] == report.id)
    assert patched["status"] == "triaged"


async def test_patch_open_to_closed_returns_updated_item(admin_client, db, admin_user):
    player = await make_user(db, discord_id="333333333333333333", username="player")
    report = await make_report(db, player.discord_id, status="open")

    resp = await admin_client.patch(f"{URL}/{report.id}", json={"status": "closed"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "closed"

    list_resp = await admin_client.get(URL)
    items = list_resp.json()["items"]
    patched = next(item for item in items if item["id"] == report.id)
    assert patched["status"] == "closed"


async def test_patch_missing_report_returns_404(admin_client, db, admin_user):
    resp = await admin_client.patch(f"{URL}/999999", json={"status": "triaged"})
    assert resp.status_code == 404


async def test_patch_closed_to_open_reopens(admin_client, db, admin_user):
    player = await make_user(db, discord_id="333333333333333333", username="player")
    report = await make_report(db, player.discord_id, status="closed")

    resp = await admin_client.patch(f"{URL}/{report.id}", json={"status": "open"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "open"

    list_resp = await admin_client.get(URL)
    items = list_resp.json()["items"]
    patched = next(item for item in items if item["id"] == report.id)
    assert patched["status"] == "open"


async def test_patch_triaged_to_open_reopens(admin_client, db, admin_user):
    player = await make_user(db, discord_id="333333333333333333", username="player")
    report = await make_report(db, player.discord_id, status="triaged")

    resp = await admin_client.patch(f"{URL}/{report.id}", json={"status": "open"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "open"


async def test_patch_triaged_to_closed_returns_updated_item(admin_client, db, admin_user):
    player = await make_user(db, discord_id="333333333333333333", username="player")
    report = await make_report(db, player.discord_id, status="triaged")

    resp = await admin_client.patch(f"{URL}/{report.id}", json={"status": "closed"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "closed"


async def test_patch_closed_to_triaged_returns_updated_item(admin_client, db, admin_user):
    player = await make_user(db, discord_id="333333333333333333", username="player")
    report = await make_report(db, player.discord_id, status="closed")

    resp = await admin_client.patch(f"{URL}/{report.id}", json={"status": "triaged"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "triaged"


async def test_patch_status_bogus_returns_422(admin_client, db, admin_user):
    player = await make_user(db, discord_id="333333333333333333", username="player")
    report = await make_report(db, player.discord_id, status="open")

    resp = await admin_client.patch(f"{URL}/{report.id}", json={"status": "bogus"})
    assert resp.status_code == 422
