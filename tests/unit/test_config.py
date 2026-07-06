"""
Unit tests for Settings validation.

Contracts:
  1. Constructing Settings with session_flicker_gc_margin_seconds <= session_stitch_window_seconds
     raises pydantic.ValidationError (invariant: GC margin must exceed stitch window).
  2. Constructing Settings with a valid margin (margin > window) succeeds.
"""
from ipaddress import ip_network

import pytest
from pydantic import ValidationError

from app.core.config import Settings

# Minimal required fields without defaults.
_REQUIRED = dict(database_url="postgresql+asyncpg://x:x@db/db", redis_url="redis://redis:6379/0")


@pytest.fixture
def no_env_overrides(monkeypatch):
    """Defaults tests must not see the deployment's real config: the api container
    injects .env via env_file, which otherwise overrides the tested defaults (and
    prints the actual secrets in assertion output). Pair with _env_file=None."""
    for var in ("LINK_CODE_SECRET", "DEV_LOGIN_SECRET", "TRUSTED_PROXY_IPS"):
        monkeypatch.delenv(var, raising=False)


def test_gc_margin_equal_to_stitch_window_raises():
    with pytest.raises(ValidationError):
        Settings(
            **_REQUIRED,
            session_stitch_window_seconds=200,
            session_flicker_gc_margin_seconds=200,  # equal — not strictly greater
        )


def test_gc_margin_less_than_stitch_window_raises():
    with pytest.raises(ValidationError):
        Settings(
            **_REQUIRED,
            session_stitch_window_seconds=200,
            session_flicker_gc_margin_seconds=100,  # less than window
        )


def test_gc_margin_greater_than_stitch_window_succeeds():
    s = Settings(
        **_REQUIRED,
        session_stitch_window_seconds=180,
        session_flicker_gc_margin_seconds=86400,
    )
    assert s.session_flicker_gc_margin_seconds > s.session_stitch_window_seconds


def test_defaults_satisfy_invariant():
    """Default values must themselves pass the invariant."""
    s = Settings(**_REQUIRED)
    assert s.session_flicker_gc_margin_seconds > s.session_stitch_window_seconds


def test_link_code_secret_defaults_empty(no_env_overrides):
    s = Settings(_env_file=None, **_REQUIRED)
    assert s.link_code_secret == ""


def test_dev_login_secret_defaults_empty(no_env_overrides):
    s = Settings(_env_file=None, **_REQUIRED)
    assert s.dev_login_secret == ""


def test_trusted_proxy_ips_defaults_empty(no_env_overrides):
    s = Settings(_env_file=None, **_REQUIRED)
    assert s.trusted_proxy_ips == ""


def test_trusted_proxy_networks_parses_plain_ips_and_strips():
    s = Settings(**_REQUIRED, trusted_proxy_ips="10.0.0.1, 10.0.0.2 , 10.0.0.3")
    assert set(s.trusted_proxy_networks) == {
        ip_network("10.0.0.1/32"),
        ip_network("10.0.0.2/32"),
        ip_network("10.0.0.3/32"),
    }


def test_trusted_proxy_networks_parses_cidr_blocks():
    s = Settings(**_REQUIRED, trusted_proxy_ips="172.16.0.0/12, 10.0.0.1")
    assert set(s.trusted_proxy_networks) == {
        ip_network("172.16.0.0/12"),
        ip_network("10.0.0.1/32"),
    }


def test_trusted_proxy_networks_ignores_blanks():
    s = Settings(**_REQUIRED, trusted_proxy_ips="10.0.0.1,,  ,10.0.0.2")
    assert set(s.trusted_proxy_networks) == {
        ip_network("10.0.0.1/32"),
        ip_network("10.0.0.2/32"),
    }


def test_empty_trusted_proxy_ips_yields_no_networks():
    s = Settings(**_REQUIRED, trusted_proxy_ips="")
    assert s.trusted_proxy_networks == ()


def test_malformed_trusted_proxy_entry_raises_at_construction():
    with pytest.raises(ValidationError):
        Settings(**_REQUIRED, trusted_proxy_ips="10.0.0.1, not-an-ip")
