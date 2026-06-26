"""
tests/api/test_games_create.py

POST /api/v1/games — create or link a game row.

Patches app.api.v1.endpoints.games._igdb_fetch_by_id so tests never
hit the real IGDB API.
"""
from datetime import date
from unittest.mock import patch

from sqlalchemy import func, select

from app.models.game import EnrichmentStatus, Game, GameAlias
from app.services.game_matching import IGDBResult
from tests.factories import make_alias, make_game

PATCH_TARGET = "app.api.v1.endpoints.games._igdb_fetch_by_id"
URL = "/api/v1/games"

_META = IGDBResult(
    cover_url="https://images.igdb.com/igdb/image/upload/t_cover_big/witcher3.jpg",
    confidence=1.0,
    genres=["Role-playing (RPG)", "Adventure"],
    themes=["Fantasy"],
    developers=["CD Projekt RED"],
    publishers=["CD Projekt"],
    first_release_date=date(2015, 5, 19),
)


# ── igdb_id mode ──────────────────────────────────────────────────────────────

async def test_igdb_new_creates_enriched(authed_client, db):
    """Fresh igdb_id → 201, ENRICHED, genres/external_api_id set in DB."""
    with patch(PATCH_TARGET, return_value=("The Witcher 3", _META)):
        resp = await authed_client.post(URL, json={"igdb_id": 1942})

    assert resp.status_code == 201
    data = resp.json()
    assert data["primary_name"] == "The Witcher 3"
    assert data["enrichment_status"] == "ENRICHED"

    game = (
        await db.execute(select(Game).where(Game.external_api_id == "1942"))
    ).scalar_one()
    assert game.genres == ["Role-playing (RPG)", "Adventure"]
    assert game.cover_image_url is not None
    assert game.first_release_date == date(2015, 5, 19)


async def test_igdb_existing_links_no_duplicate(authed_client, db):
    """Pre-existing row with that external_api_id → 200, same game_id, no new row."""
    g = await make_game(db, "The Witcher 3", enrichment_status=EnrichmentStatus.ENRICHED)
    g.external_api_id = "1942"
    await db.flush()

    with patch(PATCH_TARGET) as mock_fetch:
        resp = await authed_client.post(URL, json={"igdb_id": 1942})
        mock_fetch.assert_not_called()

    assert resp.status_code == 200
    assert resp.json()["id"] == g.id

    count = (
        await db.execute(
            select(func.count()).where(Game.external_api_id == "1942")
        )
    ).scalar_one()
    assert count == 1


async def test_igdb_not_found_404(authed_client):
    """IGDB returns nothing for this id → 404."""
    with patch(PATCH_TARGET, return_value=None):
        resp = await authed_client.post(URL, json={"igdb_id": 999999})
    assert resp.status_code == 404


async def test_igdb_rate_limited_503(authed_client):
    """_RateLimited raised by IGDB fetch → 503."""
    from app.services.game_matching import _RateLimited
    with patch(PATCH_TARGET, side_effect=_RateLimited("IGDB")):
        resp = await authed_client.post(URL, json={"igdb_id": 1942})
    assert resp.status_code == 503


# ── unrecognized mode ─────────────────────────────────────────────────────────

async def test_unrecognized_creates_needs_review(authed_client, db):
    """unrecognized + name → 201, NEEDS_REVIEW, no external_api_id, alias exists."""
    resp = await authed_client.post(
        URL, json={"unrecognized": True, "name": "Obscure Indie"}
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["enrichment_status"] == "NEEDS_REVIEW"

    game = (
        await db.execute(select(Game).where(Game.primary_name == "Obscure Indie"))
    ).scalar_one()
    assert game.enrichment_status == EnrichmentStatus.NEEDS_REVIEW
    assert game.external_api_id is None

    alias = (
        await db.execute(
            select(GameAlias).where(GameAlias.discord_process_name == "Obscure Indie")
        )
    ).scalar_one()
    assert alias.game_id == game.id


# ── alias logic ───────────────────────────────────────────────────────────────

async def test_query_stored_as_alias(authed_client, db):
    """igdb_id mode + query → alias row created for that query string."""
    with patch(PATCH_TARGET, return_value=("The Witcher 3", _META)):
        resp = await authed_client.post(
            URL, json={"igdb_id": 1942, "query": "kh 1.5"}
        )

    assert resp.status_code == 201
    game_id = resp.json()["id"]

    alias = (
        await db.execute(
            select(GameAlias).where(GameAlias.discord_process_name == "kh 1.5")
        )
    ).scalar_one()
    assert alias.game_id == game_id


async def test_duplicate_alias_not_reinserted(authed_client, db):
    """Alias already exists globally → create skips insert, no IntegrityError."""
    g_other = await make_game(db, "Other Game")
    await make_alias(db, g_other.id, "dup.exe")

    resp = await authed_client.post(
        URL, json={"unrecognized": True, "name": "dup.exe"}
    )

    assert resp.status_code == 201

    count = (
        await db.execute(
            select(func.count()).where(GameAlias.discord_process_name == "dup.exe")
        )
    ).scalar_one()
    assert count == 1


# ── validation ────────────────────────────────────────────────────────────────

async def test_both_modes_422(authed_client):
    """igdb_id + unrecognized=True both set → 422."""
    resp = await authed_client.post(
        URL, json={"igdb_id": 1942, "unrecognized": True, "name": "Some Game"}
    )
    assert resp.status_code == 422


async def test_neither_mode_422(authed_client):
    """Neither igdb_id nor unrecognized mode provided → 422."""
    resp = await authed_client.post(URL, json={})
    assert resp.status_code == 422


async def test_unrecognized_without_name_422(authed_client):
    """unrecognized=True but name missing or blank → 422."""
    resp = await authed_client.post(URL, json={"unrecognized": True})
    assert resp.status_code == 422

    resp2 = await authed_client.post(URL, json={"unrecognized": True, "name": "   "})
    assert resp2.status_code == 422


# ── auth ──────────────────────────────────────────────────────────────────────

async def test_requires_auth(client):
    """`client` has no Bearer token — expect 403."""
    resp = await client.post(URL, json={"igdb_id": 1942})
    assert resp.status_code == 403
