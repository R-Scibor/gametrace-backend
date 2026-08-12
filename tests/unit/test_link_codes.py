"""Unit tests for link_codes service — code lifecycle (issue/redeem/discard)."""
import hashlib
import hmac
import secrets

import fakeredis.aioredis
import pytest
from starlette.requests import Request

from app.core.config import settings
from app.services import link_codes

_SECRET = "test-link-code-secret"
_DISCORD_ID = "123456789012345678"


@pytest.fixture
async def r():
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


@pytest.fixture(autouse=True)
def configured_secret(monkeypatch):
    monkeypatch.setattr(settings, "link_code_secret", _SECRET)


def _digest(code: str, secret: str = _SECRET) -> str:
    return hmac.new(secret.encode(), code.encode(), hashlib.sha256).hexdigest()


async def test_issue_code_format_zero_padded_six_digits(r, monkeypatch):
    monkeypatch.setattr(secrets, "randbelow", lambda n: 42)
    code = await link_codes.issue_code(r, _DISCORD_ID)
    assert len(code) == 6
    assert code.isdigit()
    assert code == "000042"


async def test_hmac_digest_deterministic(r, monkeypatch):
    monkeypatch.setattr(secrets, "randbelow", lambda n: 493_072)
    code = await link_codes.issue_code(r, _DISCORD_ID)
    assert code == "493072"
    stored = await r.get(link_codes.code_key(_digest(code)))
    assert stored == _DISCORD_ID


async def test_hmac_digest_secret_dependent(r, monkeypatch):
    monkeypatch.setattr(secrets, "randbelow", lambda n: 111_111)
    code = await link_codes.issue_code(r, _DISCORD_ID)
    monkeypatch.setattr(settings, "link_code_secret", "other-secret")
    assert await link_codes.redeem_code(r, code) is None


async def test_issue_redeem_round_trip(r):
    code = await link_codes.issue_code(r, _DISCORD_ID)
    assert await link_codes.redeem_code(r, code) == _DISCORD_ID


async def test_redeem_is_single_use(r):
    code = await link_codes.issue_code(r, _DISCORD_ID)
    assert await link_codes.redeem_code(r, code) == _DISCORD_ID
    assert await link_codes.redeem_code(r, code) is None


async def test_wrong_code_returns_none(r):
    await link_codes.issue_code(r, _DISCORD_ID)
    assert await link_codes.redeem_code(r, "999999") is None


async def test_issue_raises_when_secret_empty(r, monkeypatch):
    monkeypatch.setattr(settings, "link_code_secret", "")
    with pytest.raises(link_codes.LinkCodesNotConfigured):
        await link_codes.issue_code(r, _DISCORD_ID)


async def test_redeem_raises_when_secret_empty(r, monkeypatch):
    code = await link_codes.issue_code(r, _DISCORD_ID)
    monkeypatch.setattr(settings, "link_code_secret", "")
    with pytest.raises(link_codes.LinkCodesNotConfigured):
        await link_codes.redeem_code(r, code)


async def test_reissue_invalidates_previous_code(r, monkeypatch):
    monkeypatch.setattr(secrets, "randbelow", lambda n: 100_000)
    first = await link_codes.issue_code(r, _DISCORD_ID)
    monkeypatch.setattr(secrets, "randbelow", lambda n: 200_000)
    second = await link_codes.issue_code(r, _DISCORD_ID)
    assert first != second
    assert await link_codes.redeem_code(r, first) is None
    assert await link_codes.redeem_code(r, second) == _DISCORD_ID


async def test_issue_sets_ttl_on_code_and_reverse_keys(r):
    code = await link_codes.issue_code(r, _DISCORD_ID)
    digest_key = link_codes.code_key(_digest(code))
    user_key = link_codes.user_key(_DISCORD_ID)
    assert 0 < await r.ttl(digest_key) <= link_codes.CODE_TTL_SECONDS
    assert 0 < await r.ttl(user_key) <= link_codes.CODE_TTL_SECONDS
    assert await r.get(user_key) == digest_key


async def test_redeem_deletes_reverse_key(r):
    code = await link_codes.issue_code(r, _DISCORD_ID)
    user_key = link_codes.user_key(_DISCORD_ID)
    assert await r.exists(user_key)
    await link_codes.redeem_code(r, code)
    assert not await r.exists(user_key)


async def test_discard_pending_code_returns_1_when_code_exists(r):
    await link_codes.issue_code(r, _DISCORD_ID)
    assert await link_codes.discard_pending_code(r, _DISCORD_ID) == 1


async def test_discard_pending_code_returns_0_when_none(r):
    assert await link_codes.discard_pending_code(r, _DISCORD_ID) == 0


async def test_discard_pending_code_works_with_empty_secret(r, monkeypatch):
    await link_codes.issue_code(r, _DISCORD_ID)
    monkeypatch.setattr(settings, "link_code_secret", "")
    assert await link_codes.discard_pending_code(r, _DISCORD_ID) == 1
    assert await link_codes.discard_pending_code(r, _DISCORD_ID) == 0


async def test_discard_removes_code_key(r):
    code = await link_codes.issue_code(r, _DISCORD_ID)
    digest_key = link_codes.code_key(_digest(code))
    assert await r.exists(digest_key)
    await link_codes.discard_pending_code(r, _DISCORD_ID)
    assert not await r.exists(digest_key)


def _make_request(client_host: str, xff: str | None = None) -> Request:
    headers: list[tuple[bytes, bytes]] = []
    if xff is not None:
        headers.append((b"x-forwarded-for", xff.encode()))
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "path": "/",
        "headers": headers,
        "client": (client_host, 12345),
        "server": ("testserver", 80),
    }
    return Request(scope)


async def test_tenth_per_ip_failure_locks_that_ip_only(r):
    locked_ip = "203.0.113.10"
    fresh_ip = "203.0.113.99"

    for _ in range(link_codes.IP_FAIL_LIMIT - 1):
        await link_codes.record_failure(r, locked_ip)
    assert await link_codes.check_lockout(r, locked_ip) is None
    assert await link_codes.check_lockout(r, fresh_ip) is None

    await link_codes.record_failure(r, locked_ip)
    assert await link_codes.check_lockout(r, locked_ip) is not None
    assert await link_codes.check_lockout(r, fresh_ip) is None


async def test_hundredth_global_failure_locks_fresh_ip(r):
    for i in range(link_codes.GLOBAL_FAIL_LIMIT - 1):
        await link_codes.record_failure(r, f"10.0.0.{i % 254 + 1}")

    fresh_ip = "198.51.100.42"
    assert await link_codes.check_lockout(r, fresh_ip) is None

    await link_codes.record_failure(r, "10.0.0.200")
    assert await link_codes.check_lockout(r, fresh_ip) is not None


async def test_lockout_retry_after_within_window(r):
    ip = "203.0.113.50"
    for _ in range(link_codes.IP_FAIL_LIMIT):
        await link_codes.record_failure(r, ip)

    retry_after = await link_codes.check_lockout(r, ip)
    assert retry_after is not None
    assert 1 <= retry_after <= link_codes.IP_FAIL_WINDOW_SECONDS


async def test_expire_set_once_per_window(r):
    ip = "203.0.113.77"
    await link_codes.record_failure(r, ip)

    ip_key = link_codes.ip_fail_key(ip)
    global_key = link_codes.global_fail_key()
    assert await r.ttl(ip_key) == link_codes.IP_FAIL_WINDOW_SECONDS
    assert await r.ttl(global_key) == link_codes.GLOBAL_FAIL_WINDOW_SECONDS

    await r.expire(ip_key, 100)
    await r.expire(global_key, 40)

    await link_codes.record_failure(r, ip)
    assert await r.ttl(ip_key) == 100
    assert await r.ttl(global_key) == 40


@pytest.mark.parametrize(
    ("trusted_proxy_ips", "client_host", "xff", "expected"),
    [
        ("10.0.0.1", "10.0.0.1", "203.0.113.1, 198.51.100.9", "198.51.100.9"),
        ("10.0.0.1", "10.0.0.1", None, "10.0.0.1"),
        ("10.0.0.1", "203.0.113.5", "198.51.100.9", "203.0.113.5"),
        ("", "203.0.113.5", "198.51.100.9", "203.0.113.5"),
        # Trusted internal hops appended to XFF (e.g. cloudflared behind NPM)
        # are skipped right-to-left; the first untrusted entry is the client.
        (
            "10.0.0.1,172.18.0.5",
            "10.0.0.1",
            "6.6.6.6, 203.0.113.1, 172.18.0.5",
            "203.0.113.1",
        ),
        ("10.0.0.1,172.18.0.5", "10.0.0.1", "203.0.113.1,172.18.0.5", "203.0.113.1"),
        # Every XFF entry trusted → fall back to the peer address.
        ("10.0.0.1,172.18.0.5", "10.0.0.1", "172.18.0.5, 10.0.0.1", "10.0.0.1"),
        # CIDR block covers the peer: docker bridge IPs are dynamic, so trust
        # the private range instead of an exact address.
        ("172.16.0.0/12", "172.18.0.5", "203.0.113.1", "203.0.113.1"),
        # Peer outside the CIDR block is not a trusted proxy — XFF ignored.
        ("172.16.0.0/12", "192.168.1.5", "203.0.113.1", "192.168.1.5"),
        # Internal XFF hops inside the CIDR block are skipped right-to-left.
        ("172.16.0.0/12", "172.18.0.5", "203.0.113.1, 172.19.0.2", "203.0.113.1"),
        # Plain IP + CIDR mix.
        ("10.0.0.1,172.16.0.0/12", "10.0.0.1", "203.0.113.1, 172.18.0.5", "203.0.113.1"),
        # Garbage XFF entry from a client never parses as trusted — returned as-is.
        ("172.16.0.0/12", "172.18.0.5", "not-an-ip", "not-an-ip"),
    ],
)
def test_get_client_ip_trust_matrix(monkeypatch, trusted_proxy_ips, client_host, xff, expected):
    monkeypatch.setattr(settings, "trusted_proxy_ips", trusted_proxy_ips)
    request = _make_request(client_host, xff)
    assert link_codes.get_client_ip(request) == expected


async def test_issue_code_never_returns_reserved_demo_code(r, monkeypatch):
    monkeypatch.setattr(settings, "demo_link_code", "555555")
    codes = iter(["555555", "111111"])
    monkeypatch.setattr(link_codes, "_generate_code", lambda: next(codes))
    code = await link_codes.issue_code(r, _DISCORD_ID)
    assert code == "111111"