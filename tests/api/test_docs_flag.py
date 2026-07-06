"""ENABLE_API_DOCS flag — Swagger/ReDoc/openapi.json exist only when enabled.

The docs routes are wired at app construction (import of app.main), so the
disabled case reloads the module with the flag off and inspects the rebuilt app.
"""
import importlib

import pytest

import app.main as main_module
from app.core.config import settings

DOC_PATHS = {"/docs", "/redoc", "/openapi.json"}


@pytest.mark.asyncio
async def test_docs_served_by_default(client):
    resp = await client.get("/openapi.json")
    assert resp.status_code == 200
    assert "/api/v1/auth/login" in resp.json()["paths"]


def test_docs_routes_absent_when_flag_off(monkeypatch):
    monkeypatch.setattr(settings, "enable_api_docs", False)
    try:
        reloaded = importlib.reload(main_module)
        paths = {route.path for route in reloaded.app.routes}
        assert not (paths & DOC_PATHS)
    finally:
        # Restore the docs-enabled app for any module importing app.main later.
        monkeypatch.undo()
        importlib.reload(main_module)
