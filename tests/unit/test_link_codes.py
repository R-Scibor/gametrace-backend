"""Unit tests for link_codes service — code lifecycle (issue/redeem/discard)."""
import hashlib
import hmac
import secrets

import fakeredis.aioredis
import pytest

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