"""
tests/unit/test_igdb_url_transform.py

Phase 3 — tests for IGDB cover URL normalization inside _igdb_search:
  - protocol-relative URLs get "https:" prepended
  - /t_thumb/ is replaced with /t_cover_big/
  - missing cover field leaves best_cover as None
"""
from unittest.mock import MagicMock, patch

from app.tasks.enrichment import _igdb_search


def _make_igdb_mock_client(items: list) -> MagicMock:
    """Return a mock httpx.Client context manager that yields canned IGDB response."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = items
    mock_resp.raise_for_status.return_value = None

    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = False
    mock_client.post.return_value = mock_resp
    return mock_client


def _call_igdb(items: list) -> tuple:
    mock_client = _make_igdb_mock_client(items)
    with patch("app.tasks.enrichment.settings") as s, \
         patch("app.tasks.enrichment.get_igdb_token", return_value="tok"), \
         patch("app.tasks.enrichment.httpx.Client", return_value=mock_client):
        s.igdb_client_id = "test-id"
        s.igdb_client_secret = "test-secret"
        return _igdb_search("Test Game")


def test_protocol_relative_becomes_https():
    cover_url, _ = _call_igdb([
        {"name": "Test Game", "cover": {"url": "//images.igdb.com/t_thumb/abc.jpg"}}
    ])
    assert cover_url is not None
    assert cover_url.startswith("https://images.igdb.com/")


def test_thumb_replaced_with_cover_big():
    cover_url, _ = _call_igdb([
        {"name": "Test Game", "cover": {"url": "https://images.igdb.com/t_thumb/abc.jpg"}}
    ])
    assert cover_url == "https://images.igdb.com/t_cover_big/abc.jpg"


def test_missing_cover_field():
    cover_url, _ = _call_igdb([{"name": "Test Game"}])
    assert cover_url is None
