from tests.factories import make_alias, make_game, make_session, make_user, dt


# ── happy paths ───────────────────────────────────────────────────────────────

async def test_suggests_global_game_user_never_played(authed_client, db, user):
    """A game owned by another user (no session for caller) appears in suggest."""
    other = await make_user(db, discord_id="222222222222222222", username="other")
    game = await make_game(db, "Hades")
    await make_session(db, other.discord_id, game.id, dt(hours_ago=3), dt(hours_ago=2))

    resp = await authed_client.get("/api/v1/games/suggest", params={"q": "hades"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 1
    assert game.id in [item["game_id"] for item in data["items"]]


async def test_matches_via_alias(authed_client, db, user):
    """A game is returned when q fuzzy-matches one of its aliases."""
    game = await make_game(db, "Red Dead Redemption 2")
    await make_alias(db, game.id, "RDR2.exe")

    resp = await authed_client.get("/api/v1/games/suggest", params={"q": "rdr2"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 1
    assert game.id in [item["game_id"] for item in data["items"]]
    # alias + primary_name both match but the game must appear exactly once
    assert len([i for i in data["items"] if i["game_id"] == game.id]) == 1


async def test_ranked_by_score(authed_client, db, user):
    """Exact match ('Hades') ranks above a partial sequel ('Hades II'), scores descend."""
    game_exact = await make_game(db, "Hades")
    game_partial = await make_game(db, "Hades II")

    resp = await authed_client.get("/api/v1/games/suggest", params={"q": "Hades"})

    assert resp.status_code == 200
    items = resp.json()["items"]
    ids = [item["game_id"] for item in items]
    assert game_exact.id in ids
    assert game_partial.id in ids
    assert ids.index(game_exact.id) < ids.index(game_partial.id)
    scores = [item["score"] for item in items]
    assert scores == sorted(scores, reverse=True)


async def test_noise_filtered(authed_client, db, user):
    """Totally unrelated query returns total=0, items=[]."""
    await make_game(db, "Hades")

    resp = await authed_client.get("/api/v1/games/suggest", params={"q": "zzzznomatch"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0
    assert data["items"] == []


async def test_score_floor_excludes_low_matches(authed_client, db, user):
    """A game that passes the ILIKE prefilter but scores < 0.3 is dropped.

    The query shares the token 'the' with 'Theme Park' (ILIKE matches 'Theme'),
    so 'Theme Park' reaches the Python scoring step — but _confidence is ~0.27,
    below the 0.3 floor, so it must be excluded. 'Cosmic Madness' scores ~0.9
    and is kept, proving the floor selectively drops the low-scoring game.
    """
    q = (
        "supercalifragilisticexpialidocious adventures beyond the seventh "
        "dimension of cosmic madness everlasting"
    )
    game_low = await make_game(db, "Theme Park")
    game_hit = await make_game(db, "Cosmic Madness")

    resp = await authed_client.get("/api/v1/games/suggest", params={"q": q})

    assert resp.status_code == 200
    data = resp.json()
    ids = [item["game_id"] for item in data["items"]]
    assert game_low.id not in ids
    assert game_hit.id in ids
    assert data["total"] == 1


async def test_pagination(authed_client, db, user):
    """limit=1 returns one item; total reflects the full above-floor count."""
    await make_game(db, "Hades")
    await make_game(db, "Hades II")

    resp = await authed_client.get(
        "/api/v1/games/suggest", params={"q": "Hades", "limit": 1}
    )

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 1
    assert data["total"] >= 2


# ── auth + validation ─────────────────────────────────────────────────────────

async def test_requires_auth(client, db, user):
    """`client` has no Bearer token — expect 403."""
    resp = await client.get("/api/v1/games/suggest", params={"q": "anything"})
    assert resp.status_code == 403


async def test_empty_q_is_422(authed_client):
    resp = await authed_client.get("/api/v1/games/suggest", params={"q": ""})
    assert resp.status_code == 422


async def test_whitespace_q_is_422(authed_client):
    """Whitespace-only q passes min_length but must be rejected, not 500."""
    resp = await authed_client.get("/api/v1/games/suggest", params={"q": "   "})
    assert resp.status_code == 422
