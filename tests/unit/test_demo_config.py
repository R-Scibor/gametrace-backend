"""
Unit tests for demo reviewer login config.

Contracts:
  1. Constructing Settings with demo_link_code not empty and not exactly 6
     digits (after strip) raises pydantic.ValidationError.
  2. Constructing Settings with demo_link_code="" (feature off) or a valid
     6-digit code (optionally padded with whitespace) succeeds.
  3. is_demo_code() returns False for any input when settings.demo_link_code
     is empty, and compares via hmac.compare_digest against the stripped
     configured code otherwise.
"""
import pytest
from pydantic import ValidationError

from app.core.config import Settings

# Minimal required fields without defaults.
_REQUIRED = dict(database_url="postgresql+asyncpg://x:x@db/db", redis_url="redis://redis:6379/0")


@pytest.fixture
def no_env_overrides(monkeypatch):
    """Defaults tests must not see the deployment's real config: the api container
    injects .env via env_file, which otherwise overrides the tested defaults."""
    for var in ("LINK_CODE_SECRET", "DEV_LOGIN_SECRET", "TRUSTED_PROXY_IPS", "DEMO_LINK_CODE"):
        monkeypatch.delenv(var, raising=False)


def test_demo_link_code_defaults_empty(no_env_overrides):
    s = Settings(_env_file=None, **_REQUIRED)
    assert s.demo_link_code == ""


def test_demo_link_code_too_short_raises():
    with pytest.raises(ValidationError):
        Settings(**_REQUIRED, demo_link_code="12345")


def test_demo_link_code_non_numeric_raises():
    with pytest.raises(ValidationError):
        Settings(**_REQUIRED, demo_link_code="abcdef")


def test_demo_link_code_malformed_spaced_raises():
    with pytest.raises(ValidationError):
        Settings(**_REQUIRED, demo_link_code="XXX XXX")


def test_demo_link_code_empty_accepted():
    s = Settings(**_REQUIRED, demo_link_code="")
    assert s.demo_link_code == ""


def test_demo_link_code_six_digits_accepted():
    s = Settings(**_REQUIRED, demo_link_code="123456")
    assert s.demo_link_code == "123456"


def test_demo_link_code_whitespace_padded_accepted():
    s = Settings(**_REQUIRED, demo_link_code=" 123456 \n")
    assert s.demo_link_code == " 123456 \n"


def test_is_demo_code_true_for_stripped_match(monkeypatch):
    from app.core.config import settings
    from app.services.demo import is_demo_code

    monkeypatch.setattr(settings, "demo_link_code", " 123456 \n")
    assert is_demo_code("123456") is True


def test_is_demo_code_false_when_setting_empty(monkeypatch):
    from app.core.config import settings
    from app.services.demo import is_demo_code

    monkeypatch.setattr(settings, "demo_link_code", "")
    assert is_demo_code("") is False
    assert is_demo_code("123456") is False


def test_is_demo_code_false_for_mismatch(monkeypatch):
    from app.core.config import settings
    from app.services.demo import is_demo_code

    monkeypatch.setattr(settings, "demo_link_code", "123456")
    assert is_demo_code("654321") is False
