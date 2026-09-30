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


@pytest.mark.parametrize("status_code", [403, 429])
async def test_exchange_code_non_auth_status_is_upstream(status_code):
    """A rate limit has no access_token. Parsing it as success turns 429 into
    DiscordAuthError, and the client retries a bad login into the limit."""
    def handler(request):
        return httpx.Response(
            status_code,
            json={"message": "You are being rate limited.", "retry_after": 1.0},
        )

    async with _client(handler) as c:
        with pytest.raises(discord_oauth.DiscordUpstreamError):
            await discord_oauth.exchange_code(c, "code", "verifier", "gametrace://redirect")


async def test_exchange_code_non_json_error_is_upstream():
    def handler(request):
        return httpx.Response(429, text="<html>rate limited</html>")

    async with _client(handler) as c:
        with pytest.raises(discord_oauth.DiscordUpstreamError):
            await discord_oauth.exchange_code(c, "code", "verifier", "gametrace://redirect")


async def test_exchange_code_success_without_token_is_upstream():
    def handler(request):
        return httpx.Response(200, json={"token_type": "Bearer"})

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


@pytest.mark.parametrize("status_code", [403, 429])
async def test_fetch_identity_non_auth_status_is_upstream(status_code):
    def handler(request):
        return httpx.Response(status_code, json={"message": "You are being rate limited."})

    async with _client(handler) as c:
        with pytest.raises(discord_oauth.DiscordUpstreamError):
            await discord_oauth.fetch_identity(c, "tok")


async def test_fetch_identity_missing_id_is_upstream():
    def handler(request):
        return httpx.Response(200, json={"username": "alice"})

    async with _client(handler) as c:
        with pytest.raises(discord_oauth.DiscordUpstreamError):
            await discord_oauth.fetch_identity(c, "tok")


async def test_fetch_guilds_returns_id_set():
    def handler(request):
        return httpx.Response(200, json=[{"id": 123, "name": "A"}, {"id": 456, "name": "B"}])

    async with _client(handler) as c:
        guilds = await discord_oauth.fetch_guilds(c, "tok")
    assert guilds == {"123", "456"}


async def test_fetch_guilds_5xx_raises_upstream_error():
    def handler(request):
        return httpx.Response(503, text="unavailable")

    async with _client(handler) as c:
        with pytest.raises(discord_oauth.DiscordUpstreamError):
            await discord_oauth.fetch_guilds(c, "tok")


@pytest.mark.parametrize("status_code", [403, 429])
async def test_fetch_guilds_non_auth_status_is_upstream(status_code):
    def handler(request):
        return httpx.Response(status_code, json={"message": "You are being rate limited."})

    async with _client(handler) as c:
        with pytest.raises(discord_oauth.DiscordUpstreamError):
            await discord_oauth.fetch_guilds(c, "tok")


async def test_fetch_guilds_object_body_is_upstream():
    def handler(request):
        return httpx.Response(200, json={"message": "not a list"})

    async with _client(handler) as c:
        with pytest.raises(discord_oauth.DiscordUpstreamError):
            await discord_oauth.fetch_guilds(c, "tok")
