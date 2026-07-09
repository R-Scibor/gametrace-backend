import pytest

pytestmark = pytest.mark.asyncio


async def test_health_response_has_request_id(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.headers.get("X-Request-ID")


async def test_inbound_request_id_is_honored(client):
    resp = await client.get("/health", headers={"X-Request-ID": "trace-xyz"})
    assert resp.headers["X-Request-ID"] == "trace-xyz"