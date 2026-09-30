"""Discord OAuth2 code-exchange + identity lookups (confidential client, server-side)."""
import httpx

from app.core.config import settings

DISCORD_API = "https://discord.com/api/v10"
# Per Discord docs the token endpoint is the UNVERSIONED path.
TOKEN_URL = "https://discord.com/api/oauth2/token"


class DiscordAuthError(Exception):
    """Bad/expired authorization code or rejected access token — maps to HTTP 401."""


class DiscordUpstreamError(Exception):
    """Discord unreachable, rate-limited, or returned a body we cannot use — maps to HTTP 502."""


def _raise_for_status(resp: httpx.Response, what: str) -> None:
    """400/401 are a rejected login. Every other non-2xx is Discord's problem.

    A 429 body has no access token. Reading it as success turns the rate
    limit into DiscordAuthError, and the client retries into the limit.
    """
    if 200 <= resp.status_code < 300:
        return
    if resp.status_code in (400, 401):
        raise DiscordAuthError(f"{what} rejected: {resp.text}")
    raise DiscordUpstreamError(f"{what} returned {resp.status_code}")


def _read_json(resp: httpx.Response, what: str):
    try:
        return resp.json()
    except ValueError as exc:
        raise DiscordUpstreamError(f"{what} returned a non-JSON body") from exc


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
    _raise_for_status(resp, "token exchange")
    body = _read_json(resp, "token exchange")
    if not isinstance(body, dict) or not body.get("access_token"):
        raise DiscordUpstreamError("token exchange returned no access_token")
    return str(body["access_token"])


async def _get(client: httpx.AsyncClient, url: str, access_token: str) -> httpx.Response:
    try:
        resp = await client.get(url, headers={"Authorization": f"Bearer {access_token}"})
    except httpx.HTTPError as exc:
        raise DiscordUpstreamError(str(exc)) from exc
    _raise_for_status(resp, f"discord {url}")
    return resp


async def fetch_identity(client: httpx.AsyncClient, access_token: str) -> dict:
    resp = await _get(client, f"{DISCORD_API}/users/@me", access_token)
    body = _read_json(resp, "discord identity")
    if not isinstance(body, dict) or "id" not in body or "username" not in body:
        raise DiscordUpstreamError("discord identity response missing id or username")
    return {"id": str(body["id"]), "username": body["username"]}


async def fetch_guilds(client: httpx.AsyncClient, access_token: str) -> set[str]:
    resp = await _get(client, f"{DISCORD_API}/users/@me/guilds", access_token)
    body = _read_json(resp, "discord guilds")
    if not isinstance(body, list):
        raise DiscordUpstreamError("discord guilds response was not a list")
    try:
        return {str(g["id"]) for g in body}
    except (TypeError, KeyError) as exc:
        raise DiscordUpstreamError("discord guilds response missing id") from exc
