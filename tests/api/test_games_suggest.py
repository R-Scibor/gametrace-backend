from app.models.game import EnrichmentStatus
from tests.factories import (
    make_alias,
    make_game,
    make_pref,
    make_session,
    make_user,
    dt,
)


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


# ── prefilter precision ───────────────────────────────────────────────────────

async def test_mid_word_token_does_not_match(authed_client, db, user):
    """A token must match at a word boundary, not anywhere inside a word.

    Regression: `the` matched as a substring inside `Wu*the*ring Waves`, which
    then scored 0.48 — above the floor — so junk surfaced in the picker while
    typing. Raising the floor cannot fix this; the prefilter has to.
    """
    noise = await make_game(db, "Wuthering Waves")
    hit = await make_game(db, "Tom Clancy's The Division")

    resp = await authed_client.get(
        "/api/v1/games/suggest", params={"q": "the Division"}
    )

    assert resp.status_code == 200
    ids = [item["game_id"] for item in resp.json()["items"]]
    assert noise.id not in ids
    assert hit.id in ids


async def test_prefix_typing_still_matches(authed_client, db, user):
    """A partially typed trailing token still matches by word prefix."""
    hit = await make_game(db, "Tom Clancy's The Division")

    resp = await authed_client.get("/api/v1/games/suggest", params={"q": "the Div"})

    assert resp.status_code == 200
    ids = [item["game_id"] for item in resp.json()["items"]]
    assert hit.id in ids


async def test_all_tokens_must_match(authed_client, db, user):
    """One matching token is not enough to admit a game when others exist.

    'The Sims 4' matches `the` as a whole word and scores 0.53 — above the
    floor — so under the old any-token prefilter it surfaced for 'the
    division'. It matches no other token, so it must now be dropped.
    """
    partial = await make_game(db, "The Sims 4")
    hit = await make_game(db, "The Division")

    resp = await authed_client.get(
        "/api/v1/games/suggest", params={"q": "the division"}
    )

    assert resp.status_code == 200
    ids = [item["game_id"] for item in resp.json()["items"]]
    assert partial.id not in ids
    assert hit.id in ids


async def test_falls_back_to_any_token_when_nothing_matches_all(
    authed_client, db, user
):
    """A typo in one token must not zero out the whole result set."""
    hit = await make_game(db, "Hades")

    resp = await authed_client.get(
        "/api/v1/games/suggest", params={"q": "hades zzzznomatch"}
    )

    assert resp.status_code == 200
    ids = [item["game_id"] for item in resp.json()["items"]]
    assert hit.id in ids


async def test_fallback_applies_a_higher_floor(authed_client, db, user):
    """The any-token fallback must not re-admit the noise the AND rule removed.

    When the catalog has no game matching every token — the normal case for
    the wizard, where the game usually isn't in the catalog yet — the fallback
    fires and any game matching just `the` is back in play. Measured against
    the live catalog those score 0.30–0.45, while genuine typo rescues score
    0.75+, so the fallback path uses a 0.6 floor instead of 0.3.
    """
    await make_game(db, "The Witcher 3: Wild Hunt")

    resp = await authed_client.get(
        "/api/v1/games/suggest", params={"q": "the Division"}
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0
    assert data["items"] == []


async def test_fallback_floor_clears_alias_admitted_noise(authed_client, db, user):
    """Alias-admitted noise sits higher than name-only noise — the floor must clear it.

    'Skyrim Special Edition' enters the fallback via its alias ("The Elder
    Scrolls V: Skyrim - Special Edition" — a real word-boundary `The`), then
    scores 0.6 on its *primary name* against 'the Division'. That is the top
    of the measured noise band; genuine rescues start at 0.75.
    """
    game = await make_game(db, "Skyrim Special Edition")
    await make_alias(db, game.id, "The Elder Scrolls V: Skyrim - Special Edition")

    resp = await authed_client.get(
        "/api/v1/games/suggest", params={"q": "the Division"}
    )

    assert resp.status_code == 200
    ids = [item["game_id"] for item in resp.json()["items"]]
    assert game.id not in ids


async def test_fallback_floor_stays_below_the_sequel_cap(authed_client, db, user):
    """Pins the ceiling the fallback floor must stay under.

    A typo'd token sends this to the fallback. _confidence caps any digitless
    query against a numbered title at _NUMBER_MISMATCH_CAP (0.75), so *every*
    numbered-sequel typo rescue scores exactly 0.75 — never higher, however
    good the match. The fallback floor therefore has only 0.05 of headroom;
    raising it to 0.8 would silently drop this whole class of rescue.
    """
    game = await make_game(db, "Red Dead Redemption 2")

    resp = await authed_client.get(
        "/api/v1/games/suggest", params={"q": "red dead redemtion"}
    )

    assert resp.status_code == 200
    items = resp.json()["items"]
    assert game.id in [item["game_id"] for item in items]
    assert items[0]["score"] == 0.75


async def test_regex_metacharacters_in_q_are_escaped(authed_client, db, user):
    """User input reaches a regex operator — it must be escaped, not executed."""
    await make_game(db, "Hades")

    resp = await authed_client.get(
        "/api/v1/games/suggest", params={"q": "hades ((("}
    )

    assert resp.status_code == 200


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


# ── unclaimed unresolved rows ─────────────────────────────────────────────────

async def test_hides_other_users_aliasless_needs_review_stub(authed_client, db, user):
    """A NEEDS_REVIEW row with no alias is a manual free-text stub — nobody's bot
    routes to it, so it must not leak into another user's typeahead."""
    other = await make_user(db, discord_id="222222222222222222", username="other")
    game = await make_game(db, "Obscure Indie", EnrichmentStatus.NEEDS_REVIEW)
    await make_session(db, other.discord_id, game.id, dt(hours_ago=3), dt(hours_ago=2))

    resp = await authed_client.get("/api/v1/games/suggest", params={"q": "obscure"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0
    assert game.id not in [item["game_id"] for item in data["items"]]


async def test_shows_other_users_needs_review_stub_with_alias(authed_client, db, user):
    """An alias means a bot bound a real process to this row.

    Bot-created stubs settle into NEEDS_REVIEW when enrichment fails, and they
    keep their alias. Hiding those would split libraries: the bot keeps writing
    sessions onto the stub while another user, unable to see it, creates a
    second ENRICHED row for the same game.
    """
    other = await make_user(db, discord_id="222222222222222222", username="other")
    game = await make_game(db, "Obscure Indie", EnrichmentStatus.NEEDS_REVIEW)
    await make_alias(db, game.id, "Obscure Indie")
    await make_session(db, other.discord_id, game.id, dt(hours_ago=3), dt(hours_ago=2))

    resp = await authed_client.get("/api/v1/games/suggest", params={"q": "obscure"})

    assert resp.status_code == 200
    ids = [item["game_id"] for item in resp.json()["items"]]
    assert game.id in ids


async def test_shows_other_users_pending_stub(authed_client, db, user):
    """PENDING stays globally visible — only NEEDS_REVIEW is ever hidden.

    Hiding PENDING would recreate the library split at the other unresolved
    status: a freshly created row is PENDING until the enrichment worker runs.
    """
    other = await make_user(db, discord_id="222222222222222222", username="other")
    game = await make_game(db, "Obscure Indie")
    await make_session(db, other.discord_id, game.id, dt(hours_ago=3), dt(hours_ago=2))

    resp = await authed_client.get("/api/v1/games/suggest", params={"q": "obscure"})

    assert resp.status_code == 200
    ids = [item["game_id"] for item in resp.json()["items"]]
    assert game.id in ids


async def test_shows_own_aliasless_needs_review_stub_via_session(
    authed_client, db, user
):
    """The caller's own stub stays visible — a session on it is the claim."""
    game = await make_game(db, "Obscure Indie", EnrichmentStatus.NEEDS_REVIEW)
    await make_session(db, user.discord_id, game.id, dt(hours_ago=3), dt(hours_ago=2))

    resp = await authed_client.get("/api/v1/games/suggest", params={"q": "obscure"})

    assert resp.status_code == 200
    ids = [item["game_id"] for item in resp.json()["items"]]
    assert game.id in ids


async def test_shows_own_aliasless_needs_review_stub_via_preference(
    authed_client, db, user
):
    """A preference row is also a claim, even with no session yet."""
    game = await make_game(db, "Obscure Indie", EnrichmentStatus.NEEDS_REVIEW)
    await make_pref(db, user.discord_id, game.id)

    resp = await authed_client.get("/api/v1/games/suggest", params={"q": "obscure"})

    assert resp.status_code == 200
    ids = [item["game_id"] for item in resp.json()["items"]]
    assert game.id in ids


async def test_hidden_stub_excluded_from_total(authed_client, db, user):
    """The filter runs in SQL, so `total` reflects the filtered set, not the raw one."""
    other = await make_user(db, discord_id="222222222222222222", username="other")
    hidden = await make_game(db, "Hades Unofficial", EnrichmentStatus.NEEDS_REVIEW)
    await make_session(db, other.discord_id, hidden.id, dt(hours_ago=3), dt(hours_ago=2))
    visible = await make_game(db, "Hades", EnrichmentStatus.ENRICHED)

    resp = await authed_client.get("/api/v1/games/suggest", params={"q": "hades"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    ids = [item["game_id"] for item in data["items"]]
    assert ids == [visible.id]
    assert hidden.id not in ids


async def test_fallback_still_runs_when_only_hidden_rows_match_strictly(
    authed_client, db, user
):
    """Regression guard: the filter must run *inside* the strict pass.

    The hidden stub matches all three tokens; the ENRICHED game matches only
    two and needs the any-token fallback to surface (scoring 0.75, above
    _SUGGEST_FALLBACK_FLOOR). If the visibility filter were applied after
    scoring, the stub would make the strict pass non-empty, the fallback would
    never fire, and this would return [].
    """
    other = await make_user(db, discord_id="222222222222222222", username="other")
    hidden = await make_game(
        db, "Red Dead Redemtion", EnrichmentStatus.NEEDS_REVIEW
    )
    await make_session(db, other.discord_id, hidden.id, dt(hours_ago=3), dt(hours_ago=2))
    visible = await make_game(db, "Red Dead Redemption 2", EnrichmentStatus.ENRICHED)

    resp = await authed_client.get(
        "/api/v1/games/suggest", params={"q": "red dead redemtion"}
    )

    assert resp.status_code == 200
    ids = [item["game_id"] for item in resp.json()["items"]]
    assert visible.id in ids
    assert hidden.id not in ids
