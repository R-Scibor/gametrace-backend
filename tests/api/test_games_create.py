"""
tests/api/test_games_create.py

POST /api/v1/games — create or link a game row.

Patches app.api.v1.endpoints.games._igdb_fetch_by_id so tests never
hit the real IGDB API.
"""
from datetime import date
from unittest.mock import patch

import pytest
from sqlalchemy import func, select

from app.models.game import EnrichmentStatus, Game, GameAlias
from app.services.demo import DEMO_DISCORD_ID, DEMO_USERNAME
from app.services.game_matching import IGDBResult
from tests.factories import make_alias, make_game, make_token, make_user

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


# ── rate limiting ─────────────────────────────────────────────────────────────

@pytest.fixture
def rate_limit_enabled():
    """Enable the limiter for one test. authed_client mints a fresh random token
    per test, so each test gets its own limiter bucket — no reset needed."""
    from app.main import app
    limiter = app.state.limiter
    limiter.enabled = True
    yield
    limiter.enabled = False


async def test_rate_limited_after_60(authed_client, rate_limit_enabled):
    """Looser than /match: igdb_id mode dedupes before calling IGDB and
    unrecognized mode never calls it, so a genuine library backfill must fit.
    The cap exists to bound a looping client, not to pace honest use."""
    with patch(PATCH_TARGET, return_value=("The Witcher 3", _META)):
        statuses = [
            (await authed_client.post(
                URL, json={"unrecognized": True, "name": f"Stub {i}"},
            )).status_code
            for i in range(61)
        ]

    assert statuses[:60] == [201] * 60
    assert statuses[60] == 429


# ── demo account alias suppression ──────────────────────────────────────────

@pytest.fixture
async def demo_client(db, client):
    """HTTP client authed as the shared reviewer/demo account."""
    demo_user = await make_user(db, discord_id=DEMO_DISCORD_ID, username=DEMO_USERNAME)
    token = await make_token(db, demo_user.discord_id)
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


async def test_demo_unrecognized_creates_game_no_alias(demo_client, db):
    """Demo user, unrecognized mode → 201 and game created, but no GameAlias row."""
    resp = await demo_client.post(
        URL, json={"unrecognized": True, "name": "Reviewer Indie"}
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["enrichment_status"] == "NEEDS_REVIEW"

    game = (
        await db.execute(select(Game).where(Game.primary_name == "Reviewer Indie"))
    ).scalar_one()
    assert game.enrichment_status == EnrichmentStatus.NEEDS_REVIEW

    alias_count = (
        await db.execute(
            select(func.count()).where(
                GameAlias.discord_process_name == "Reviewer Indie"
            )
        )
    ).scalar_one()
    assert alias_count == 0


async def test_demo_igdb_query_creates_game_no_alias(demo_client, db):
    """Demo user, igdb_id mode + query → game created, no alias for the query."""
    with patch(PATCH_TARGET, return_value=("The Witcher 3", _META)):
        resp = await demo_client.post(
            URL, json={"igdb_id": 1942, "query": "reviewer query"}
        )

    assert resp.status_code == 201
    data = resp.json()
    assert data["primary_name"] == "The Witcher 3"

    alias_count = (
        await db.execute(
            select(func.count()).where(
                GameAlias.discord_process_name == "reviewer query"
            )
        )
    ).scalar_one()
    assert alias_count == 0


async def test_normal_user_still_gets_alias_both_modes(authed_client, db):
    """Regression guard: a normal (non-demo) user is unaffected in either mode."""
    resp1 = await authed_client.post(
        URL, json={"unrecognized": True, "name": "Normal User Game"}
    )
    assert resp1.status_code == 201
    alias1 = (
        await db.execute(
            select(GameAlias).where(
                GameAlias.discord_process_name == "Normal User Game"
            )
        )
    ).scalar_one()
    assert alias1 is not None

    with patch(PATCH_TARGET, return_value=("The Witcher 3", _META)):
        resp2 = await authed_client.post(
            URL, json={"igdb_id": 1942, "query": "normal query"}
        )
    assert resp2.status_code == 201
    alias2 = (
        await db.execute(
            select(GameAlias).where(GameAlias.discord_process_name == "normal query")
        )
    ).scalar_one()
    assert alias2 is not None
