"""HTTP client for the Discord bot's read-only commands (/stats, /recent).

Calls the FastAPI service over the compose network instead of querying
Postgres directly, so visibility rules (library_only, is_ignored,
NEEDS_REVIEW) stay defined in exactly one place — the API layer.

Auth: X-Bot-Service-Secret + X-Discord-Id headers, resolved server-side by
`get_bot_user` (app/api/v1/endpoints/auth.py). Never log the secret.
"""
import logging

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 5.0

# Overridable transport for tests (httpx.MockTransport). None = real network.
_transport: httpx.BaseTransport | None = None


class BotApiError(Exception):
    """Raised when a bot→API call fails: timeout, unreachable host, or non-2xx response."""


def _headers(discord_id: str) -> dict[str, str]:
    return {
        "X-Bot-Service-Secret": settings.bot_service_secret,
        "X-Discord-Id": discord_id,
    }


async def _get(path: str, discord_id: str, params=None):
    url = f"{settings.api_base_url}{path}"
    try:
        async with httpx.AsyncClient(
            timeout=REQUEST_TIMEOUT_SECONDS, transport=_transport
        ) as client:
            response = await client.get(url, headers=_headers(discord_id), params=params)
    except httpx.TimeoutException as exc:
        raise BotApiError(f"Timed out calling {path}") from exc
    except httpx.HTTPError as exc:
        raise BotApiError(f"Unreachable calling {path}") from exc

    if not response.is_success:
        raise BotApiError(f"{path} returned {response.status_code}")

    try:
        return response.json()
    except ValueError as exc:
        raise BotApiError(f"{path} returned a non-JSON body") from exc


async def get_summary(discord_id: str) -> dict:
    """GET /stats/summary?days=7 for the given Discord user."""
    return await _get("/api/v1/stats/summary", discord_id, params={"days": 7})


async def get_recent_sessions(discord_id: str) -> list[dict]:
    """GET /sessions?status=COMPLETED&status=ERROR&library_only=true&limit=5."""
    params = [
        ("status", "COMPLETED"),
        ("status", "ERROR"),
        ("library_only", "true"),
        ("limit", "5"),
    ]
    return await _get("/api/v1/sessions", discord_id, params=params)


async def get_review_count(discord_id: str) -> int:
    """GET /games?status=NEEDS_REVIEW&limit=1 and return the server-computed total."""
    data = await _get(
        "/api/v1/games", discord_id, params={"status": "NEEDS_REVIEW", "limit": 1}
    )
    try:
        return data["total"]
    except (KeyError, TypeError) as exc:
        raise BotApiError("/api/v1/games response missing 'total' field") from exc
