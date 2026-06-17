import httpx
import pytest

from app.services import discord_oauth


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_exchange_code_returns_access_token():
    def handler(request):
        assert request.url == discord_oauth.TOKEN_URL
        assert request.headers["authorization"].startswith("Basic ")
        return httpx.Response(200, json={"access_token": "abc123", "token_type": "Bearer"})

    async with _client(handler) as c:
        token = await discord_oauth.exchange_code(c, "code", "verifier", "gametrace://redirect")
    assert token == "abc123"


async def test_exchange_code_400_raises_auth_error():
    def handler(request):
        return httpx.Response(400, json={"error": "invalid_grant"})

    async with _client(handler) as c:
        with pytest.raises(discord_oauth.DiscordAuthError):
            await discord_oauth.exchange_code(c, "bad", "verifier", "gametrace://redirect")


async def test_exchange_code_5xx_raises_upstream_error():
    def handler(request):
        return httpx.Response(503, text="unavailable")

    async with _client(handler) as c:
        with pytest.raises(discord_oauth.DiscordUpstreamError):
            await discord_oauth.exchange_code(c, "code", "verifier", "gametrace://redirect")


async def test_exchange_code_network_error_raises_upstream_error():
    def handler(request):
        raise httpx.ConnectError("boom")

    async with _client(handler) as c:
        with pytest.raises(discord_oauth.DiscordUpstreamError):
            await discord_oauth.exchange_code(c, "code", "verifier", "gametrace://redirect")


async def test_fetch_identity_returns_id_and_username():
    def handler(request):
        return httpx.Response(200, json={"id": 999, "username": "alice", "email": "x@y.z"})

    async with _client(handler) as c:
        ident = await discord_oauth.fetch_identity(c, "tok")
    assert ident == {"id": "999", "username": "alice"}


async def test_fetch_identity_401_raises_auth_error():
    def handler(request):
        return httpx.Response(401, json={"message": "401: Unauthorized"})

    async with _client(handler) as c:
        with pytest.raises(discord_oauth.DiscordAuthError):
            await discord_oauth.fetch_identity(c, "tok")


async def test_fetch_guilds_returns_id_set():
    def handler(request):
        return httpx.Response(200, json=[{"id": 123, "name": "A"}, {"id": 456, "name": "B"}])

    async with _client(handler) as c:
        guilds = await discord_oauth.fetch_guilds(c, "tok")
    assert guilds == {"123", "456"}
