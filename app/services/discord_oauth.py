"""Discord OAuth2 code-exchange + identity lookups (confidential client, server-side)."""
import httpx

from app.core.config import settings

DISCORD_API = "https://discord.com/api/v10"
# Per Discord docs the token endpoint is the UNVERSIONED path.
TOKEN_URL = "https://discord.com/api/oauth2/token"


class DiscordAuthError(Exception):
    """Bad/expired authorization code or rejected access token — maps to HTTP 401."""


class DiscordUpstreamError(Exception):
    """Discord unreachable or returned 5xx — maps to HTTP 502."""


async def exchange_code(
    client: httpx.AsyncClient, code: str, code_verifier: str, redirect_uri: str
) -> str:
    # Client credentials go in HTTP Basic auth (per Discord docs), NOT the form body.
    # code_verifier is the PKCE proof (Discord requires >=43 chars, S256 challenge).
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "code_verifier": code_verifier,
    }
    try:
        resp = await client.post(
            TOKEN_URL,
            data=data,
            auth=(settings.discord_client_id, settings.discord_client_secret),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    except httpx.HTTPError as exc:
        raise DiscordUpstreamError(str(exc)) from exc
    if resp.status_code in (400, 401):
        raise DiscordAuthError(f"token exchange rejected: {resp.text}")
    if resp.status_code >= 500:
        raise DiscordUpstreamError(f"discord token endpoint returned {resp.status_code}")
    access_token = resp.json().get("access_token")
    if not access_token:
        raise DiscordAuthError("no access_token in token response")
    return access_token


async def _get(client: httpx.AsyncClient, url: str, access_token: str) -> httpx.Response:
    try:
        resp = await client.get(url, headers={"Authorization": f"Bearer {access_token}"})
    except httpx.HTTPError as exc:
        raise DiscordUpstreamError(str(exc)) from exc
    if resp.status_code in (400, 401):
        raise DiscordAuthError(f"discord rejected access token at {url}")
    if resp.status_code >= 500:
        raise DiscordUpstreamError(f"discord {url} returned {resp.status_code}")
    return resp


async def fetch_identity(client: httpx.AsyncClient, access_token: str) -> dict:
    resp = await _get(client, f"{DISCORD_API}/users/@me", access_token)
    body = resp.json()
    return {"id": str(body["id"]), "username": body["username"]}


async def fetch_guilds(client: httpx.AsyncClient, access_token: str) -> set[str]:
    resp = await _get(client, f"{DISCORD_API}/users/@me/guilds", access_token)
    return {str(g["id"]) for g in resp.json()}
