from unittest.mock import patch

from tests.factories import dt, make_report, make_user

URL = "/api/v1/admin/reports"
LOG_PATCH_TARGET = "app.api.v1.endpoints.admin.reports.log_admin_action"


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


# ── PATCH partial-update semantics: admin_note + presence rules ────────────────

async def test_patch_status_explicit_null_returns_422(admin_client, db, admin_user):
    player = await make_user(db, discord_id="333333333333333333", username="player")
    report = await make_report(db, player.discord_id, status="open")

    resp = await admin_client.patch(f"{URL}/{report.id}", json={"status": None})
    assert resp.status_code == 422


async def test_patch_empty_body_returns_422(admin_client, db, admin_user):
    player = await make_user(db, discord_id="333333333333333333", username="player")
    report = await make_report(db, player.discord_id, status="open")

    resp = await admin_client.patch(f"{URL}/{report.id}", json={})
    assert resp.status_code == 422


async def test_patch_status_only_leaves_existing_note_intact(admin_client, db, admin_user):
    player = await make_user(db, discord_id="333333333333333333", username="player")
    report = await make_report(
        db, player.discord_id, status="open", admin_note="existing note"
    )

    resp = await admin_client.patch(f"{URL}/{report.id}", json={"status": "triaged"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "triaged"
    assert body["admin_note"] == "existing note"


async def test_patch_note_only_leaves_status_intact(admin_client, db, admin_user):
    player = await make_user(db, discord_id="333333333333333333", username="player")
    report = await make_report(db, player.discord_id, status="triaged")

    resp = await admin_client.patch(f"{URL}/{report.id}", json={"admin_note": "x"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "triaged"
    assert body["admin_note"] == "x"


async def test_patch_note_empty_string_clears_to_null(admin_client, db, admin_user):
    player = await make_user(db, discord_id="333333333333333333", username="player")
    report = await make_report(db, player.discord_id, admin_note="something")

    resp = await admin_client.patch(f"{URL}/{report.id}", json={"admin_note": ""})
    assert resp.status_code == 200
    assert resp.json()["admin_note"] is None


async def test_patch_note_explicit_null_clears_to_null(admin_client, db, admin_user):
    player = await make_user(db, discord_id="333333333333333333", username="player")
    report = await make_report(db, player.discord_id, admin_note="something")

    resp = await admin_client.patch(f"{URL}/{report.id}", json={"admin_note": None})
    assert resp.status_code == 200
    assert resp.json()["admin_note"] is None


async def test_patch_note_whitespace_only_clears_to_null(admin_client, db, admin_user):
    player = await make_user(db, discord_id="333333333333333333", username="player")
    report = await make_report(db, player.discord_id, admin_note="something")

    resp = await admin_client.patch(f"{URL}/{report.id}", json={"admin_note": "   "})
    assert resp.status_code == 200
    assert resp.json()["admin_note"] is None


async def test_patch_note_overwrites_existing_note(admin_client, db, admin_user):
    player = await make_user(db, discord_id="333333333333333333", username="player")
    report = await make_report(db, player.discord_id, admin_note="old note")

    resp = await admin_client.patch(f"{URL}/{report.id}", json={"admin_note": "new note"})
    assert resp.status_code == 200
    assert resp.json()["admin_note"] == "new note"


async def test_patch_note_over_4000_chars_returns_422(admin_client, db, admin_user):
    player = await make_user(db, discord_id="333333333333333333", username="player")
    report = await make_report(db, player.discord_id)

    resp = await admin_client.patch(
        f"{URL}/{report.id}", json={"admin_note": "x" * 4001}
    )
    assert resp.status_code == 422


async def test_patch_note_exactly_4000_chars_accepted(admin_client, db, admin_user):
    player = await make_user(db, discord_id="333333333333333333", username="player")
    report = await make_report(db, player.discord_id)

    resp = await admin_client.patch(
        f"{URL}/{report.id}", json={"admin_note": "x" * 4000}
    )
    assert resp.status_code == 200
    assert resp.json()["admin_note"] == "x" * 4000


async def test_patch_both_status_and_note_updates_both(admin_client, db, admin_user):
    player = await make_user(db, discord_id="333333333333333333", username="player")
    report = await make_report(db, player.discord_id, status="open")

    resp = await admin_client.patch(
        f"{URL}/{report.id}", json={"status": "triaged", "admin_note": "both changed"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "triaged"
    assert body["admin_note"] == "both changed"


# ── PATCH no-op semantics: same value → 200, zero audit lines ────────────────

async def test_patch_same_status_is_noop_returns_200_no_audit(admin_client, db, admin_user):
    player = await make_user(db, discord_id="333333333333333333", username="player")
    report = await make_report(db, player.discord_id, status="open")

    with patch(LOG_PATCH_TARGET) as mock_log:
        resp = await admin_client.patch(f"{URL}/{report.id}", json={"status": "open"})

    assert resp.status_code == 200
    assert resp.json()["status"] == "open"
    mock_log.assert_not_called()


async def test_patch_same_note_is_noop_returns_200_no_audit(admin_client, db, admin_user):
    player = await make_user(db, discord_id="333333333333333333", username="player")
    report = await make_report(db, player.discord_id, admin_note="unchanged note")

    with patch(LOG_PATCH_TARGET) as mock_log:
        resp = await admin_client.patch(
            f"{URL}/{report.id}", json={"admin_note": "unchanged note"}
        )

    assert resp.status_code == 200
    assert resp.json()["admin_note"] == "unchanged note"
    mock_log.assert_not_called()


async def test_patch_note_noop_when_clearing_already_null_note(admin_client, db, admin_user):
    player = await make_user(db, discord_id="333333333333333333", username="player")
    report = await make_report(db, player.discord_id, admin_note=None)

    with patch(LOG_PATCH_TARGET) as mock_log:
        resp = await admin_client.patch(f"{URL}/{report.id}", json={"admin_note": ""})

    assert resp.status_code == 200
    assert resp.json()["admin_note"] is None
    mock_log.assert_not_called()


# ── PATCH audit semantics: content never logged, correct actions ─────────────

async def test_patch_status_change_emits_report_triage_with_before_after(
    admin_client, db, admin_user
):
    player = await make_user(db, discord_id="333333333333333333", username="player")
    report = await make_report(db, player.discord_id, status="open")

    with patch(LOG_PATCH_TARGET) as mock_log:
        resp = await admin_client.patch(f"{URL}/{report.id}", json={"status": "triaged"})

    assert resp.status_code == 200
    mock_log.assert_called_once_with(
        admin_user.discord_id,
        "report_triage",
        f"report:{report.id}",
        before="open",
        after="triaged",
    )


async def test_patch_note_set_emits_report_note_with_set_marker_no_content(
    admin_client, db, admin_user
):
    player = await make_user(db, discord_id="333333333333333333", username="player")
    report = await make_report(db, player.discord_id, admin_note=None)

    with patch(LOG_PATCH_TARGET) as mock_log:
        resp = await admin_client.patch(
            f"{URL}/{report.id}", json={"admin_note": "secret triage details"}
        )

    assert resp.status_code == 200
    mock_log.assert_called_once_with(
        admin_user.discord_id,
        "report_note",
        f"report:{report.id}",
        before="empty",
        after="set",
    )
    for call in mock_log.call_args_list:
        for arg in list(call.args) + list(call.kwargs.values()):
            assert arg != "secret triage details"


async def test_patch_note_clear_emits_report_note_with_empty_marker(
    admin_client, db, admin_user
):
    player = await make_user(db, discord_id="333333333333333333", username="player")
    report = await make_report(db, player.discord_id, admin_note="had something")

    with patch(LOG_PATCH_TARGET) as mock_log:
        resp = await admin_client.patch(f"{URL}/{report.id}", json={"admin_note": None})

    assert resp.status_code == 200
    mock_log.assert_called_once_with(
        admin_user.discord_id,
        "report_note",
        f"report:{report.id}",
        before="set",
        after="empty",
    )


async def test_patch_both_changed_emits_both_audit_lines(admin_client, db, admin_user):
    player = await make_user(db, discord_id="333333333333333333", username="player")
    report = await make_report(db, player.discord_id, status="open", admin_note=None)

    with patch(LOG_PATCH_TARGET) as mock_log:
        resp = await admin_client.patch(
            f"{URL}/{report.id}",
            json={"status": "triaged", "admin_note": "flagged"},
        )

    assert resp.status_code == 200
    assert mock_log.call_count == 2
    mock_log.assert_any_call(
        admin_user.discord_id,
        "report_triage",
        f"report:{report.id}",
        before="open",
        after="triaged",
    )
    mock_log.assert_any_call(
        admin_user.discord_id,
        "report_note",
        f"report:{report.id}",
        before="empty",
        after="set",
    )


# ── DELETE /admin/reports/{id} ─────────────────────────────────────────────────

async def test_delete_unauthenticated_returns_401(client, db):
    resp = await client.delete(
        f"{URL}/1", headers={"Authorization": "Bearer badtoken"}
    )
    assert resp.status_code == 401


async def test_delete_non_admin_returns_403(authed_client, db, user):
    player = await make_user(db, discord_id="333333333333333333", username="player")
    report = await make_report(db, player.discord_id)

    resp = await authed_client.delete(f"{URL}/{report.id}")
    assert resp.status_code == 403


async def test_delete_returns_204_and_removes_from_list(admin_client, db, admin_user):
    player = await make_user(db, discord_id="333333333333333333", username="player")
    report = await make_report(db, player.discord_id)

    resp = await admin_client.delete(f"{URL}/{report.id}")
    assert resp.status_code == 204
    assert resp.content == b""

    list_resp = await admin_client.get(URL)
    assert list_resp.status_code == 200
    ids = [item["id"] for item in list_resp.json()["items"]]
    assert report.id not in ids


async def test_delete_open_report_drops_open_reports_count(admin_client, db, admin_user):
    player = await make_user(db, discord_id="333333333333333333", username="player")
    report = await make_report(db, player.discord_id, status="open")

    overview_before = await admin_client.get("/api/v1/admin/stats/overview")
    assert overview_before.json()["open_reports_count"] == 1

    resp = await admin_client.delete(f"{URL}/{report.id}")
    assert resp.status_code == 204

    overview_after = await admin_client.get("/api/v1/admin/stats/overview")
    assert overview_after.json()["open_reports_count"] == 0


async def test_delete_missing_report_returns_404(admin_client, db, admin_user):
    resp = await admin_client.delete(f"{URL}/999999")
    assert resp.status_code == 404


async def test_delete_twice_returns_404_on_second_call(admin_client, db, admin_user):
    player = await make_user(db, discord_id="333333333333333333", username="player")
    report = await make_report(db, player.discord_id)

    first = await admin_client.delete(f"{URL}/{report.id}")
    assert first.status_code == 204

    second = await admin_client.delete(f"{URL}/{report.id}")
    assert second.status_code == 404


async def test_delete_emits_report_delete_with_before_status_and_message_preview(
    admin_client, db, admin_user
):
    player = await make_user(db, discord_id="333333333333333333", username="player")
    long_message = "x" * 200
    report = await make_report(
        db, player.discord_id, message=long_message, status="triaged"
    )

    with patch(LOG_PATCH_TARGET) as mock_log:
        resp = await admin_client.delete(f"{URL}/{report.id}")

    assert resp.status_code == 204
    mock_log.assert_called_once_with(
        admin_user.discord_id,
        "report_delete",
        f"report:{report.id}",
        before="triaged",
        detail=long_message[:80],
    )
