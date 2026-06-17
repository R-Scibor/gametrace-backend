import pytest

from app.core.config import settings
from app.services import discord_oauth
from tests.factories import make_user

REDIRECT = "gametrace://redirect"


@pytest.fixture(autouse=True)
def _oauth_config(monkeypatch):
    monkeypatch.setattr(settings, "discord_oauth_redirect_uris", REDIRECT)
    monkeypatch.setattr(settings, "discord_guild_ids", "123")


def _patch_discord(monkeypatch, *, identity, guilds):
    async def fake_exchange(client, code, code_verifier, redirect_uri):
        return "access_tok"

    async def fake_identity(client, access_token):
        return identity

    async def fake_guilds(client, access_token):
        return guilds

    monkeypatch.setattr(discord_oauth, "exchange_code", fake_exchange)
    monkeypatch.setattr(discord_oauth, "fetch_identity", fake_identity)
    monkeypatch.setattr(discord_oauth, "fetch_guilds", fake_guilds)


def _body():
    return {"code": "c", "code_verifier": "v", "redirect_uri": REDIRECT}


async def test_new_user_is_auto_created(client, db, monkeypatch):
    _patch_discord(monkeypatch, identity={"id": "555", "username": "newbie"}, guilds={"123"})

    resp = await client.post("/api/v1/auth/discord", json=_body())

    assert resp.status_code == 200
    data = resp.json()
    assert data["discord_id"] == "555"
    assert data["username"] == "newbie"
    assert data["needs_server_join"] is False
    assert "token" in data

    from app.models.user import User
    user = await db.get(User, "555")
    assert user is not None
    assert user.username == "newbie"


async def test_existing_user_username_synced(client, db, monkeypatch):
    await make_user(db, discord_id="555", username="oldname")
    _patch_discord(monkeypatch, identity={"id": "555", "username": "newname"}, guilds={"123"})

    resp = await client.post("/api/v1/auth/discord", json=_body())

    assert resp.status_code == 200
    assert resp.json()["username"] == "newname"

    from app.models.user import User
    refreshed = await db.get(User, "555")
    assert refreshed.username == "newname"


async def test_not_in_guild_sets_needs_server_join(client, db, monkeypatch):
    _patch_discord(monkeypatch, identity={"id": "555", "username": "newbie"}, guilds={"999"})

    resp = await client.post("/api/v1/auth/discord", json=_body())

    assert resp.status_code == 200
    assert resp.json()["needs_server_join"] is True


async def test_redirect_uri_not_allowlisted_returns_400(client, monkeypatch):
    _patch_discord(monkeypatch, identity={"id": "555", "username": "x"}, guilds={"123"})

    resp = await client.post(
        "/api/v1/auth/discord",
        json={"code": "c", "code_verifier": "v", "redirect_uri": "https://evil.example"},
    )

    assert resp.status_code == 400


async def test_bad_code_returns_401(client, monkeypatch):
    async def boom(client, code, code_verifier, redirect_uri):
        raise discord_oauth.DiscordAuthError("invalid_grant")

    monkeypatch.setattr(discord_oauth, "exchange_code", boom)

    resp = await client.post("/api/v1/auth/discord", json=_body())

    assert resp.status_code == 401


async def test_discord_unreachable_returns_502(client, monkeypatch):
    async def boom(client, code, code_verifier, redirect_uri):
        raise discord_oauth.DiscordUpstreamError("connect error")

    monkeypatch.setattr(discord_oauth, "exchange_code", boom)

    resp = await client.post("/api/v1/auth/discord", json=_body())

    assert resp.status_code == 502


async def test_username_collision_returns_409(client, db, monkeypatch):
    await make_user(db, discord_id="AAA", username="taken")
    _patch_discord(monkeypatch, identity={"id": "555", "username": "taken"}, guilds={"123"})

    resp = await client.post("/api/v1/auth/discord", json=_body())

    assert resp.status_code == 409


async def test_fetch_identity_auth_error_returns_401(client, monkeypatch):
    async def fake_exchange(client, code, code_verifier, redirect_uri):
        return "access_tok"

    async def boom(client, access_token):
        raise discord_oauth.DiscordAuthError("rejected")

    monkeypatch.setattr(discord_oauth, "exchange_code", fake_exchange)
    monkeypatch.setattr(discord_oauth, "fetch_identity", boom)

    resp = await client.post("/api/v1/auth/discord", json=_body())

    assert resp.status_code == 401


async def test_fetch_guilds_upstream_error_returns_502(client, monkeypatch):
    async def fake_exchange(client, code, code_verifier, redirect_uri):
        return "access_tok"

    async def fake_identity(client, access_token):
        return {"id": "555", "username": "newbie"}

    async def boom(client, access_token):
        raise discord_oauth.DiscordUpstreamError("503 from discord")

    monkeypatch.setattr(discord_oauth, "exchange_code", fake_exchange)
    monkeypatch.setattr(discord_oauth, "fetch_identity", fake_identity)
    monkeypatch.setattr(discord_oauth, "fetch_guilds", boom)

    resp = await client.post("/api/v1/auth/discord", json=_body())

    assert resp.status_code == 502
