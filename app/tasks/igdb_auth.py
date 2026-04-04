"""
Twitch / IGDB token management.

Uses sync redis-py (already a Celery dependency) and sync httpx.
Designed to be called from asyncio.to_thread() in enrichment.py.

Race condition note: multiple workers may simultaneously find an empty Redis key
and all fetch a fresh token. This is harmless — the last write wins and all tokens
are valid. Using SET NX would prevent redundant fetches but adds complexity for
minimal gain given the 5-minute buffer on expiry.
"""
import httpx
import redis as redis_sync

from app.core.config import settings

IGDB_TOKEN_KEY = "igdb:access_token"


def get_igdb_token() -> str:
    r = redis_sync.from_url(settings.redis_url, decode_responses=True)
    token = r.get(IGDB_TOKEN_KEY)
    if token:
        return token
    return _refresh(r)


def invalidate_igdb_token() -> None:
    """Call on 401 — forces a fresh fetch on the next get_igdb_token() call."""
    r = redis_sync.from_url(settings.redis_url, decode_responses=True)
    r.delete(IGDB_TOKEN_KEY)


def _refresh(r: redis_sync.Redis) -> str:
    resp = httpx.post(
        "https://id.twitch.tv/oauth2/token",
        params={
            "client_id": settings.igdb_client_id,
            "client_secret": settings.igdb_client_secret,
            "grant_type": "client_credentials",
        },
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    token = data["access_token"]
    # Store with a 5-minute buffer before actual expiry
    r.setex(IGDB_TOKEN_KEY, data["expires_in"] - 300, token)
    return token
