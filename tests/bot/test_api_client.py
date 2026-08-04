"""Unit tests for app.bot.api_client — mocked transport, no live API."""
import httpx
import pytest

from app.bot import api_client
from app.core.config import settings

DISCORD_ID = "111111111111111111"


@pytest.fixture(autouse=True)
def bot_secret(monkeypatch):
    monkeypatch.setattr(settings, "bot_service_secret", "test-bot-secret")


@pytest.fixture(autouse=True)
def _reset_transport():
    yield
    api_client._transport = None


def _install_transport(handler):
    api_client._transport = httpx.MockTransport(handler)


# ── get_summary ────────────────────────────────────────────────────────────

async def test_get_summary_sends_auth_headers_and_days_param():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["query"] = request.url.query.decode()
        seen["secret"] = request.headers.get("x-bot-service-secret")
        seen["discord_id"] = request.headers.get("x-discord-id")
        return httpx.Response(200, json={"total_seconds": 42})

    _install_transport(handler)

    result = await api_client.get_summary(DISCORD_ID)

    assert result == {"total_seconds": 42}
    assert seen["path"] == "/api/v1/stats/summary"
    assert seen["query"] == "days=7"
    assert seen["secret"] == "test-bot-secret"
    assert seen["discord_id"] == DISCORD_ID


# ── get_recent_sessions ──────────────────────────────────────────────────────

async def test_get_recent_sessions_sends_expected_query_params():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["params"] = list(httpx.QueryParams(request.url.query).multi_items())
        return httpx.Response(200, json=[{"id": 1}])

    _install_transport(handler)

    result = await api_client.get_recent_sessions(DISCORD_ID)

    assert result == [{"id": 1}]
    assert seen["path"] == "/api/v1/sessions"
    assert seen["params"] == [
        ("status", "COMPLETED"),
        ("status", "ERROR"),
        ("library_only", "true"),
        ("limit", "5"),
    ]


# ── get_review_count ──────────────────────────────────────────────────────────

async def test_get_review_count_returns_total_field():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/games"
        params = dict(httpx.QueryParams(request.url.query).multi_items())
        assert params == {"status": "NEEDS_REVIEW", "limit": "1"}
        return httpx.Response(200, json={"total": 3, "items": []})

    _install_transport(handler)

    result = await api_client.get_review_count(DISCORD_ID)

    assert result == 3


# ── Error handling ────────────────────────────────────────────────────────────

async def test_timeout_raises_bot_api_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out")

    _install_transport(handler)

    with pytest.raises(api_client.BotApiError):
        await api_client.get_summary(DISCORD_ID)


async def test_connect_error_raises_bot_api_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    _install_transport(handler)

    with pytest.raises(api_client.BotApiError):
        await api_client.get_summary(DISCORD_ID)


async def test_404_raises_bot_api_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "Not Found"})

    _install_transport(handler)

    with pytest.raises(api_client.BotApiError):
        await api_client.get_summary(DISCORD_ID)


async def test_401_raises_bot_api_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "Unauthorized"})

    _install_transport(handler)

    with pytest.raises(api_client.BotApiError):
        await api_client.get_summary(DISCORD_ID)


async def test_error_message_never_includes_secret():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    _install_transport(handler)

    with pytest.raises(api_client.BotApiError) as exc_info:
        await api_client.get_summary(DISCORD_ID)

    assert "test-bot-secret" not in str(exc_info.value)


async def test_2xx_non_json_body_raises_bot_api_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json")

    _install_transport(handler)

    with pytest.raises(api_client.BotApiError):
        await api_client.get_summary(DISCORD_ID)


async def test_2xx_missing_total_field_raises_bot_api_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"items": []})

    _install_transport(handler)

    with pytest.raises(api_client.BotApiError):
        await api_client.get_review_count(DISCORD_ID)


# ── Pending-deletion 403 (nested body, distinguished from other 403s) ────────

async def test_pending_deletion_403_raises_pending_deletion_error_with_fields():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={
                "detail": {
                    "detail": "Account scheduled for deletion",
                    "deletion_requested_at": "2026-08-01T00:00:00+00:00",
                    "purge_at": "2026-08-15T00:00:00+00:00",
                    "days_left": 4,
                }
            },
        )

    _install_transport(handler)

    with pytest.raises(api_client.PendingDeletionError) as exc_info:
        await api_client.get_summary(DISCORD_ID)

    assert exc_info.value.purge_at == "2026-08-15T00:00:00+00:00"
    assert exc_info.value.days_left == 4
    assert exc_info.value.status_code == 403


async def test_pending_deletion_error_is_also_a_bot_api_error():
    # Existing `except BotApiError` call sites must keep working unchanged.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={
                "detail": {
                    "detail": "Account scheduled for deletion",
                    "purge_at": "2026-08-15T00:00:00+00:00",
                    "days_left": 1,
                }
            },
        )

    _install_transport(handler)

    with pytest.raises(api_client.BotApiError):
        await api_client.get_summary(DISCORD_ID)


async def test_generic_403_does_not_raise_pending_deletion_error():
    # A genuine authorization failure must not be mislabelled as pending
    # deletion just because it happens to also be a 403.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"detail": "Admin privileges required"})

    _install_transport(handler)

    with pytest.raises(api_client.BotApiError) as exc_info:
        await api_client.get_summary(DISCORD_ID)

    assert not isinstance(exc_info.value, api_client.PendingDeletionError)
    assert exc_info.value.status_code == 403


async def test_403_with_dict_detail_but_wrong_marker_is_not_pending_deletion():
    # Nested dict `detail` that doesn't match the exact marker string must
    # still fall through to the generic error, not be misparsed.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403, json={"detail": {"detail": "Something else entirely"}}
        )

    _install_transport(handler)

    with pytest.raises(api_client.BotApiError) as exc_info:
        await api_client.get_summary(DISCORD_ID)

    assert not isinstance(exc_info.value, api_client.PendingDeletionError)
