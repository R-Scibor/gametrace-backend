"""
Unit tests for the Sentry init helper.

Two contracts that must hold so the rest of the suite stays cheap to run:
  1. With no DSN, init_sentry() is a strict no-op (does not call sentry_sdk.init).
  2. The before_send scrub redacts Authorization headers and ?token= query strings.
"""
from unittest.mock import MagicMock, patch

from app.core import observability


def test_init_sentry_noop_when_dsn_empty():
    with patch("app.core.observability.settings") as fake_settings, \
         patch("app.core.observability.sentry_sdk") as fake_sdk:
        fake_settings.sentry_dsn = ""
        observability.init_sentry("api")
        fake_sdk.init.assert_not_called()
        fake_sdk.set_tag.assert_not_called()


def test_init_sentry_calls_sdk_when_dsn_set():
    with patch("app.core.observability.settings") as fake_settings, \
         patch("app.core.observability.sentry_sdk") as fake_sdk:
        fake_settings.sentry_dsn = "https://public@sentry.example/1"
        fake_settings.sentry_environment = "homelab"
        observability.init_sentry("api")
        fake_sdk.init.assert_called_once()
        fake_sdk.set_tag.assert_called_once_with("component", "api")


def test_init_sentry_celery_adds_celery_integration():
    with patch("app.core.observability.settings") as fake_settings, \
         patch("app.core.observability.sentry_sdk") as fake_sdk:
        fake_settings.sentry_dsn = "https://public@sentry.example/1"
        fake_settings.sentry_environment = "homelab"
        observability.init_sentry("celery")
        kwargs = fake_sdk.init.call_args.kwargs
        integration_classes = {type(i).__name__ for i in kwargs["integrations"]}
        assert "CeleryIntegration" in integration_classes


def test_before_send_scrubs_authorization_header():
    event = {"request": {"headers": {"Authorization": "Bearer secret-token"}}}
    out = observability._before_send(event, {})
    assert out["request"]["headers"]["Authorization"] == "[scrubbed]"


def test_before_send_scrubs_lowercase_authorization_header():
    event = {"request": {"headers": {"authorization": "Bearer secret-token"}}}
    out = observability._before_send(event, {})
    assert out["request"]["headers"]["authorization"] == "[scrubbed]"


def test_before_send_scrubs_token_query_string():
    event = {"request": {"query_string": "foo=1&token=abc&bar=2"}}
    out = observability._before_send(event, {})
    assert out["request"]["query_string"] == "[scrubbed]"


def test_before_send_passthrough_when_no_request():
    event = {"level": "error"}
    out = observability._before_send(event, {})
    assert out == {"level": "error"}
