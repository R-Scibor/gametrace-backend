"""One-time login link codes — issue, redeem, discard (Redis-backed)."""
import hashlib
import hmac
import secrets
from ipaddress import ip_address

from starlette.requests import Request

from app.core.config import settings
from app.services.demo import is_demo_code

CODE_TTL_SECONDS = 300
IP_FAIL_LIMIT = 10
IP_FAIL_WINDOW_SECONDS = 900
GLOBAL_FAIL_LIMIT = 100
GLOBAL_FAIL_WINDOW_SECONDS = 60
_MAX_COLLISION_RETRIES = 5

# The permanent demo/reviewer code skips check_lockout entirely (see
# auth.py), so successful redemptions were otherwise unbounded — the first
# unauthenticated path doing unbounded DB writes. This is a separate, much
# looser per-IP counter just for that branch. The caller (auth.py) wraps
# every call to check_demo_rate_limit in the same fail-open try/except it
# already uses for Redis errors elsewhere: a Redis outage must never lock a
# Play reviewer out, so this limiter degrades to "no limit" rather than
# blocking anything.
DEMO_RATE_LIMIT = 30
DEMO_RATE_WINDOW_SECONDS = 3600


class LinkCodesNotConfigured(Exception):
    """Raised when link_code_secret is empty and issue/redeem is attempted."""


def code_key(digest: str) -> str:
    return f"link:code:{digest}"


def user_key(discord_id: str) -> str:
    return f"link:user:{discord_id}"


def ip_fail_key(ip: str) -> str:
    return f"link:fails:ip:{ip}"


def global_fail_key() -> str:
    return "link:fails:global"


def _require_secret() -> str:
    secret = settings.link_code_secret
    if not secret:
        raise LinkCodesNotConfigured("link_code_secret is not configured")
    return secret


def _code_digest(code: str, secret: str) -> str:
    return hmac.new(secret.encode(), code.encode(), hashlib.sha256).hexdigest()


def _generate_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


async def issue_code(r, discord_id: str) -> str:
    secret = _require_secret()
    reverse_key = user_key(discord_id)
    old_code_key = await r.get(reverse_key)

    for _ in range(_MAX_COLLISION_RETRIES):
        code = _generate_code()
        if is_demo_code(code):
            continue
        digest = _code_digest(code, secret)
        new_code_key = code_key(digest)

        pipe = r.pipeline()
        if old_code_key:
            pipe.delete(old_code_key)
        pipe.set(new_code_key, discord_id, nx=True, ex=CODE_TTL_SECONDS)
        if not (await pipe.execute())[-1]:
            continue

        await r.set(reverse_key, new_code_key, ex=CODE_TTL_SECONDS)
        return code

    raise RuntimeError("failed to allocate a unique link code after collision retries")


async def redeem_code(r, code: str) -> str | None:
    secret = _require_secret()
    digest = _code_digest(code, secret)
    digest_key = code_key(digest)
    discord_id = await r.getdel(digest_key)
    if discord_id is None:
        return None
    await r.delete(user_key(discord_id))
    return discord_id


async def check_lockout(r, ip: str) -> int | None:
    """Return Retry-After seconds when locked out, else None (per-IP then global)."""
    ip_key = ip_fail_key(ip)
    ip_count = await r.get(ip_key)
    if ip_count is not None and int(ip_count) >= IP_FAIL_LIMIT:
        return max(1, await r.ttl(ip_key))

    global_key = global_fail_key()
    global_count = await r.get(global_key)
    if global_count is not None and int(global_count) >= GLOBAL_FAIL_LIMIT:
        return max(1, await r.ttl(global_key))

    return None


async def record_failure(r, ip: str) -> None:
    ip_key = ip_fail_key(ip)
    ip_count = await r.incr(ip_key)
    if ip_count == 1:
        await r.expire(ip_key, IP_FAIL_WINDOW_SECONDS)

    global_key = global_fail_key()
    global_count = await r.incr(global_key)
    if global_count == 1:
        await r.expire(global_key, GLOBAL_FAIL_WINDOW_SECONDS)


def demo_rate_key(ip: str) -> str:
    return f"link:demo:ip:{ip}"


async def check_demo_rate_limit(r, ip: str) -> int | None:
    """Increment the per-IP demo-login counter and return Retry-After seconds
    once it exceeds DEMO_RATE_LIMIT within the hour, else None.

    Not fail-open by itself — any Redis error here propagates to the caller,
    which is expected to catch it and treat the limit as not exceeded. See
    the module docstring above DEMO_RATE_LIMIT.
    """
    key = demo_rate_key(ip)
    count = await r.incr(key)
    if count == 1:
        await r.expire(key, DEMO_RATE_WINDOW_SECONDS)
    if count > DEMO_RATE_LIMIT:
        return max(1, await r.ttl(key))
    return None


def _is_trusted_proxy(ip: str) -> bool:
    try:
        addr = ip_address(ip)
    except ValueError:
        # Not an IP at all (client-forged XFF junk) — never a trusted hop.
        return False
    return any(addr in net for net in settings.trusted_proxy_networks)


def get_client_ip(request: Request) -> str:
    peer = request.client.host if request.client else ""
    if _is_trusted_proxy(peer):
        xff = request.headers.get("x-forwarded-for")
        if xff:
            # Walk right-to-left past trusted internal hops (each proxy appends
            # its peer); the first untrusted entry is the real client. Entries
            # further left are client-supplied and spoofable — never use them.
            for entry in reversed(xff.split(",")):
                ip = entry.strip()
                if ip and not _is_trusted_proxy(ip):
                    return ip
        return peer
    return peer


async def discard_pending_code(r, discord_id: str) -> int:
    reverse_key = user_key(discord_id)
    code_key_name = await r.getdel(reverse_key)
    if not code_key_name:
        return 0
    await r.delete(code_key_name)
    return 1
