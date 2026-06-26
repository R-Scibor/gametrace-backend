"""
tests/api/test_games_match.py

POST /api/v1/games/match — synchronous IGDB candidate search.

Patches app.api.v1.endpoints.games._igdb_search_candidates so tests never
hit the real IGDB API.  Mirrors the pattern in test_voice.py which patches
app.api.v1.endpoints.voice._gemini_parse.
"""
import pytest
from unittest.mock import patch

from app.services.game_matching import IGDBCandidate


_CANDIDATE_A = IGDBCandidate(
    igdb_id=1234,
    name="Hades",
    year=2020,
    cover_url="https://images.igdb.com/igdb/image/upload/t_cover_big/abc.jpg",
    score=0.97,
)
_CANDIDATE_B = IGDBCandidate(
    igdb_id=5678,
    name="Hades II",
    year=2024,
    cover_url=None,
    score=0.72,
)

PATCH_TARGET = "app.api.v1.endpoints.games._igdb_search_candidates"


# ── happy paths ───────────────────────────────────────────────────────────────

async def test_returns_ranked_candidates(authed_client):
    """Mock returns 2 IGDBCandidates → 200 list of 2 with correct fields."""
    with patch(PATCH_TARGET, return_value=[_CANDIDATE_A, _CANDIDATE_B]):
        resp = await authed_client.post(
            "/api/v1/games/match",
            json={"query": "hades"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2

    first = data[0]
    assert first["igdb_id"] == 1234
    assert first["name"] == "Hades"
    assert first["year"] == 2020
    assert first["cover_url"] == "https://images.igdb.com/igdb/image/upload/t_cover_big/abc.jpg"
    assert first["score"] == pytest.approx(0.97, abs=1e-6)

    second = data[1]
    assert second["igdb_id"] == 5678
    assert second["name"] == "Hades II"
    assert second["year"] == 2024
    assert second["cover_url"] is None
    assert second["score"] == pytest.approx(0.72, abs=1e-6)


async def test_empty_result(authed_client):
    """Mock returns [] → 200 with empty list."""
    with patch(PATCH_TARGET, return_value=[]):
        resp = await authed_client.post(
            "/api/v1/games/match",
            json={"query": "zzzznomatch"},
        )

    assert resp.status_code == 200
    assert resp.json() == []


# ── error mapping ─────────────────────────────────────────────────────────────

async def test_rate_limited_returns_503(authed_client):
    """_RateLimited raised by the mock → 503."""
    from app.services.game_matching import _RateLimited

    with patch(PATCH_TARGET, side_effect=_RateLimited("IGDB")):
        resp = await authed_client.post(
            "/api/v1/games/match",
            json={"query": "hades"},
        )

    assert resp.status_code == 503


async def test_other_exception_returns_502(authed_client):
    """Any non-_RateLimited exception from the IGDB call → 502."""
    with patch(PATCH_TARGET, side_effect=RuntimeError("connection refused")):
        resp = await authed_client.post(
            "/api/v1/games/match",
            json={"query": "hades"},
        )

    assert resp.status_code == 502


# ── auth + validation ─────────────────────────────────────────────────────────

async def test_requires_auth(client):
    """`client` has no Bearer token — expect 403."""
    resp = await client.post(
        "/api/v1/games/match",
        json={"query": "hades"},
    )
    assert resp.status_code == 403


async def test_empty_query_is_422(authed_client):
    """query with min_length=1 violated → 422."""
    resp = await authed_client.post(
        "/api/v1/games/match",
        json={"query": ""},
    )
    assert resp.status_code == 422
