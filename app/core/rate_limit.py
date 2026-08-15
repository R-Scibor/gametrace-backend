"""Rate limiting for the IGDB-backed game endpoints (POST /games, /games/match).

Keyed on the caller's bearer credential (SHA-256 of the token), not user_id,
because slowapi's key function sees only the Request — the User resolved by
get_current_user is not visible to it. Redis storage → shared across api workers
and durable across restarts.

Caveat: a token-keyed counter does NOT bind per user. Every login path issues a
new token and none are revoked on re-login, so a user can reset a bucket by
logging in again, or run several tokens in parallel. These endpoints spend IGDB
quota rather than money, so the weaker guarantee is accepted here; the paid voice
endpoint enforces a real per-user quota instead — see app/services/voice_quota.py.
"""
from fastapi import Request
from slowapi import Limiter

from app.core.config import settings
from app.models.user import UserAuthToken


def user_token_key(request: Request) -> str:
    """Limiter key = hash of the bearer token. By the time this runs, the
    get_current_user dependency has already 401'd any invalid/absent token, so a
    valid Authorization header is present. The fallback keeps it total."""
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return UserAuthToken.hash_token(auth[7:].strip())
    return "anonymous"


limiter = Limiter(key_func=user_token_key, storage_uri=settings.redis_url)