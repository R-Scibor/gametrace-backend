async def test_get_me_returns_defaults(authed_client, user):
    resp = await authed_client.get("/api/v1/profile/me")

    assert resp.status_code == 200
    body = resp.json()
    assert body["discord_id"] == user.discord_id
    assert body["username"] == user.username
    assert body["timezone"] == "UTC"
    assert body["weekly_report_enabled"] is True
    assert body["push_enabled"] is True


async def test_put_settings_partial_update(authed_client, db, user):
    resp = await authed_client.put(
        "/api/v1/profile/settings",
        json={"timezone": "Europe/Warsaw"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["timezone"] == "Europe/Warsaw"
    # Unspecified fields untouched
    assert body["weekly_report_enabled"] is True
    assert body["push_enabled"] is True


async def test_put_settings_multiple_fields(authed_client):
    resp = await authed_client.put(
        "/api/v1/profile/settings",
        json={
            "timezone": "America/Los_Angeles",
            "weekly_report_enabled": False,
            "push_enabled": False,
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["timezone"] == "America/Los_Angeles"
    assert body["weekly_report_enabled"] is False
    assert body["push_enabled"] is False


async def test_put_settings_invalid_timezone_rejected(authed_client):
    resp = await authed_client.put(
        "/api/v1/profile/settings",
        json={"timezone": "Mars/Phobos"},
    )

    assert resp.status_code == 422


async def test_put_settings_empty_body_is_noop(authed_client):
    resp = await authed_client.put("/api/v1/profile/settings", json={})

    assert resp.status_code == 200
    assert resp.json()["timezone"] == "UTC"


async def test_put_settings_requires_auth(client):
    resp = await client.put(
        "/api/v1/profile/settings",
        json={"timezone": "Europe/Warsaw"},
    )

    assert resp.status_code == 403


async def test_me_reflects_settings_update(authed_client):
    await authed_client.put(
        "/api/v1/profile/settings",
        json={"weekly_report_enabled": False},
    )

    resp = await authed_client.get("/api/v1/profile/me")

    assert resp.status_code == 200
    assert resp.json()["weekly_report_enabled"] is False
