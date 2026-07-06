"""Request body size cap (middleware) — rejects oversized uploads with 413.

App-side backstop; the primary control is nginx client_max_body_size at the
NPM edge. This covers the naive oversized upload and any path that reaches the
container outside NPM.
"""
import pytest

from app.core.config import settings


@pytest.mark.asyncio
async def test_oversized_body_rejected_with_413(client, monkeypatch):
    monkeypatch.setattr(settings, "max_request_body_bytes", 100)

    resp = await client.post("/api/v1/voice/transcribe", content=b"x" * 200)

    assert resp.status_code == 413
    assert resp.json()["detail"] == "Request body too large."


@pytest.mark.asyncio
async def test_body_within_limit_passes_middleware(client, monkeypatch):
    monkeypatch.setattr(settings, "max_request_body_bytes", 100)

    # Under the cap — middleware lets it through; unauthenticated so auth rejects it.
    resp = await client.post("/api/v1/voice/transcribe", content=b"x" * 10)

    assert resp.status_code != 413
