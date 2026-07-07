"""Rate limiting for paid endpoints (voice STT).

Keyed on the caller's bearer credential (SHA-256 of the token), not user_id:
the threat is a leaked token in a loop = one credential = caught directly, and
this needs no changes to get_current_user (whose resolved User is not visible to
slowapi's request-only key function). Redis storage → shared across api workers
and durable across restarts.
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