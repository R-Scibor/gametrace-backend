from unittest.mock import patch

import pytest

from app.models.game import EnrichmentStatus
from app.services.game_matching import IGDBCandidate
from tests.factories import dt, make_alias, make_game, make_session, make_user

URL = "/api/v1/admin/games"


# ── Auth gate ────────────────────────────────────────────────────────────────

async def test_unauthenticated_returns_401(client, db):
    resp = await client.get(URL, headers={"Authorization": "Bearer badtoken"})
    assert resp.status_code == 401


async def test_non_admin_returns_403(authed_client, db, user):
    resp = await authed_client.get(URL)
    assert resp.status_code == 403


# ── Status filter ────────────────────────────────────────────────────────────

async def test_default_status_filter_needs_review_only(admin_client, db, admin_user):
    review = await make_game(db, "Needs Review Game", enrichment_status=EnrichmentStatus.NEEDS_REVIEW)
    await make_game(db, "Enriched Game", enrichment_status=EnrichmentStatus.ENRICHED)
    await make_game(db, "Pending Game", enrichment_status=EnrichmentStatus.PENDING)

    resp = await admin_client.get(URL)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert len(body["items"]) == 1
    assert body["items"][0]["id"] == review.id
    assert body["items"][0]["enrichment_status"] == "NEEDS_REVIEW"


async def test_status_query_narrows_to_enriched(admin_client, db, admin_user):
    await make_game(db, "Needs Review Game", enrichment_status=EnrichmentStatus.NEEDS_REVIEW)
    enriched = await make_game(db, "Enriched Game", enrichment_status=EnrichmentStatus.ENRICHED)

    resp = await admin_client.get(URL, params={"status": "ENRICHED"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == enriched.id
    assert body["items"][0]["enrichment_status"] == "ENRICHED"


# ── session_count ────────────────────────────────────────────────────────────

async def test_session_count_across_multiple_users(admin_client, db, admin_user):
    game = await make_game(db, "Shared Game", enrichment_status=EnrichmentStatus.NEEDS_REVIEW)
    user_a = await make_user(db, discord_id="333333333333333333", username="user_a")
    user_b = await make_user(db, discord_id="444444444444444444", username="user_b")
    await make_session(db, user_a.discord_id, game.id, dt(hours_ago=2), dt(hours_ago=1))
    await make_session(db, user_b.discord_id, game.id, dt(hours_ago=4), dt(hours_ago=3))

    resp = await admin_client.get(URL)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["session_count"] == 2


async def test_session_count_excludes_deleted_and_flicker(admin_client, db, admin_user):
    game = await make_game(db, "Filtered Sessions", enrichment_status=EnrichmentStatus.NEEDS_REVIEW)
    user = await make_user(db, discord_id="333333333333333333", username="player")
    await make_session(db, user.discord_id, game.id, dt(hours_ago=3), dt(hours_ago=2))
    await make_session(
        db, user.discord_id, game.id, dt(hours_ago=5), dt(hours_ago=4), deleted_at=dt(hours_ago=1)
    )
    await make_session(
        db, user.discord_id, game.id, dt(hours_ago=7), dt(hours_ago=6), is_flicker=True
    )

    resp = await admin_client.get(URL)
    assert resp.status_code == 200
    assert resp.json()["items"][0]["session_count"] == 1


# ── aliases ──────────────────────────────────────────────────────────────────

async def test_aliases_populated_sorted(admin_client, db, admin_user):
    game = await make_game(db, "Alias Game", enrichment_status=EnrichmentStatus.NEEDS_REVIEW)
    await make_alias(db, game.id, "zebra.exe")
    await make_alias(db, game.id, "alpha.exe")

    resp = await admin_client.get(URL)
    assert resp.status_code == 200
    item = resp.json()["items"][0]
    assert item["aliases"] == ["alpha.exe", "zebra.exe"]


async def test_zero_alias_game_returns_empty_aliases(admin_client, db, admin_user):
    game = await make_game(db, "No Aliases", enrichment_status=EnrichmentStatus.NEEDS_REVIEW)

    resp = await admin_client.get(URL)
    assert resp.status_code == 200
    item = resp.json()["items"][0]
    assert item["id"] == game.id
    assert item["aliases"] == []


async def test_q_match_on_two_aliases_returns_one_row_with_both_aliases(
    admin_client, db, admin_user
):
    game = await make_game(db, "Primary Only", enrichment_status=EnrichmentStatus.NEEDS_REVIEW)
    await make_alias(db, game.id, "foo-bar.exe")
    await make_alias(db, game.id, "foo-baz.exe")

    resp = await admin_client.get(URL, params={"q": "foo"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert len(body["items"]) == 1
    assert set(body["items"][0]["aliases"]) == {"foo-bar.exe", "foo-baz.exe"}


# ── q filter ─────────────────────────────────────────────────────────────────

async def test_q_matches_primary_name(admin_client, db, admin_user):
    match = await make_game(db, "Elden Ring", enrichment_status=EnrichmentStatus.NEEDS_REVIEW)
    await make_game(db, "Other Game", enrichment_status=EnrichmentStatus.NEEDS_REVIEW)

    resp = await admin_client.get(URL, params={"q": "elden"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == match.id


async def test_q_matches_alias_discord_process_name(admin_client, db, admin_user):
    game = await make_game(db, "Unrelated Name", enrichment_status=EnrichmentStatus.NEEDS_REVIEW)
    await make_alias(db, game.id, "stardewvalley.exe")

    resp = await admin_client.get(URL, params={"q": "stardew"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == game.id


async def test_blank_or_whitespace_q_applies_no_text_filter(admin_client, db, admin_user):
    g1 = await make_game(db, "Game One", enrichment_status=EnrichmentStatus.NEEDS_REVIEW)
    g2 = await make_game(db, "Game Two", enrichment_status=EnrichmentStatus.NEEDS_REVIEW)

    for blank in ("", "   ", "\t"):
        resp = await admin_client.get(URL, params={"q": blank})
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        assert {item["id"] for item in body["items"]} == {g1.id, g2.id}


# ── Pagination ───────────────────────────────────────────────────────────────

async def test_skip_and_limit_paginate(admin_client, db, admin_user):
    games = []
    for i in range(5):
        games.append(
            await make_game(db, f"Paginate-{i}", enrichment_status=EnrichmentStatus.NEEDS_REVIEW)
        )

    resp = await admin_client.get(URL, params={"sort": "id_asc", "skip": 1, "limit": 2})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 5
    assert len(body["items"]) == 2
    assert [item["id"] for item in body["items"]] == [games[1].id, games[2].id]


# ── Sort ─────────────────────────────────────────────────────────────────────

async def test_sort_sessions_desc_orders_by_count_then_id_asc(admin_client, db, admin_user):
    user = await make_user(db, discord_id="333333333333333333", username="player")
    low_id_high_count = await make_game(
        db, "Low Id High Count", enrichment_status=EnrichmentStatus.NEEDS_REVIEW
    )
    high_id_high_count = await make_game(
        db, "High Id High Count", enrichment_status=EnrichmentStatus.NEEDS_REVIEW
    )
    no_sessions = await make_game(db, "No Sessions", enrichment_status=EnrichmentStatus.NEEDS_REVIEW)

    if low_id_high_count.id > high_id_high_count.id:
        low_id_high_count, high_id_high_count = high_id_high_count, low_id_high_count

    for _ in range(2):
        await make_session(
            db, user.discord_id, low_id_high_count.id, dt(hours_ago=4), dt(hours_ago=3)
        )
    for _ in range(2):
        await make_session(
            db, user.discord_id, high_id_high_count.id, dt(hours_ago=6), dt(hours_ago=5)
        )

    resp = await admin_client.get(URL, params={"sort": "sessions_desc"})
    assert resp.status_code == 200
    ids = [item["id"] for item in resp.json()["items"]]
    assert ids.index(low_id_high_count.id) < ids.index(high_id_high_count.id)
    assert ids.index(high_id_high_count.id) < ids.index(no_sessions.id)
    assert resp.json()["items"][0]["session_count"] == 2


async def test_sort_id_asc(admin_client, db, admin_user):
    g2 = await make_game(db, "Second", enrichment_status=EnrichmentStatus.NEEDS_REVIEW)
    g1 = await make_game(db, "First", enrichment_status=EnrichmentStatus.NEEDS_REVIEW)

    resp = await admin_client.get(URL, params={"sort": "id_asc"})
    assert resp.status_code == 200
    ids = [item["id"] for item in resp.json()["items"]]
    assert ids == sorted([g1.id, g2.id])


async def test_sort_name_asc(admin_client, db, admin_user):
    zebra = await make_game(db, "Zebra", enrichment_status=EnrichmentStatus.NEEDS_REVIEW)
    alpha = await make_game(db, "Alpha", enrichment_status=EnrichmentStatus.NEEDS_REVIEW)

    resp = await admin_client.get(URL, params={"sort": "name_asc"})
    assert resp.status_code == 200
    names = [item["primary_name"] for item in resp.json()["items"]]
    assert names == ["Alpha", "Zebra"]
    assert names[0] != zebra.primary_name


# ── POST /games/{id}/enrich ──────────────────────────────────────────────────

ENRICH_URL = "/api/v1/admin/games/{game_id}/enrich"


async def test_enrich_existing_game_queues_and_returns_202(admin_client, db, admin_user):
    game = await make_game(db, "Re-queue Me", enrichment_status=EnrichmentStatus.NEEDS_REVIEW)

    with patch("app.api.v1.endpoints.admin.catalog.queue_enrichment") as mock_queue:
        resp = await admin_client.post(ENRICH_URL.format(game_id=game.id))

    assert resp.status_code == 202
    assert resp.json() == {"queued": True}
    mock_queue.assert_called_once_with(game.id)


async def test_enrich_missing_game_returns_404(admin_client, db, admin_user):
    with patch("app.api.v1.endpoints.admin.catalog.queue_enrichment") as mock_queue:
        resp = await admin_client.post(ENRICH_URL.format(game_id=999999))

    assert resp.status_code == 404
    mock_queue.assert_not_called()


async def test_enrich_non_admin_returns_403(authed_client, db, user):
    game = await make_game(db, "Forbidden Enrich", enrichment_status=EnrichmentStatus.PENDING)

    with patch("app.api.v1.endpoints.admin.catalog.queue_enrichment") as mock_queue:
        resp = await authed_client.post(ENRICH_URL.format(game_id=game.id))

    assert resp.status_code == 403
    mock_queue.assert_not_called()


# ── POST /games/match ────────────────────────────────────────────────────────

MATCH_URL = "/api/v1/admin/games/match"
MATCH_PATCH_TARGET = "app.api.v1.endpoints.admin.catalog._igdb_search_candidates"

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


async def test_match_returns_igdb_candidates(admin_client, db, admin_user):
    with patch(MATCH_PATCH_TARGET, return_value=[_CANDIDATE_A, _CANDIDATE_B]):
        resp = await admin_client.post(MATCH_URL, json={"query": "hades"})

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


async def test_match_rate_limited_returns_503(admin_client, db, admin_user):
    from app.services.game_matching import _RateLimited

    with patch(MATCH_PATCH_TARGET, side_effect=_RateLimited("IGDB")):
        resp = await admin_client.post(MATCH_URL, json={"query": "hades"})

    assert resp.status_code == 503


async def test_match_non_admin_returns_403(authed_client, db, user):
    with patch(MATCH_PATCH_TARGET, return_value=[_CANDIDATE_A]):
        resp = await authed_client.post(MATCH_URL, json={"query": "hades"})

    assert resp.status_code == 403