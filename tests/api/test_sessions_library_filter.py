from app.models.game import EnrichmentStatus
from tests.factories import dt, make_game, make_pref, make_session


async def test_library_only_excludes_ignored_game(authed_client, db, user):
    """A session on an ignored game is hidden when library_only=true."""
    game = await make_game(db)
    await make_pref(db, user.discord_id, game.id, is_ignored=True)
    await make_session(db, user.discord_id, game.id, dt(hours_ago=2), dt(hours_ago=1))

    resp = await authed_client.get("/api/v1/sessions?library_only=true")

    assert resp.status_code == 200
    assert resp.json() == []


async def test_library_only_excludes_unaccepted_needs_review_game(authed_client, db, user):
    """A session on an unaccepted NEEDS_REVIEW stub is hidden when library_only=true."""
    game = await make_game(db, enrichment_status=EnrichmentStatus.NEEDS_REVIEW)
    await make_session(db, user.discord_id, game.id, dt(hours_ago=2), dt(hours_ago=1))

    resp = await authed_client.get("/api/v1/sessions?library_only=true")

    assert resp.status_code == 200
    assert resp.json() == []


async def test_library_only_default_off_includes_both(authed_client, db, user):
    """Without library_only, both an ignored game and an unaccepted NEEDS_REVIEW
    game's sessions are present — the default must not change existing behavior."""
    ignored_game = await make_game(db, primary_name="Ignored Game")
    await make_pref(db, user.discord_id, ignored_game.id, is_ignored=True)
    ignored_session = await make_session(
        db, user.discord_id, ignored_game.id, dt(hours_ago=4), dt(hours_ago=3)
    )

    review_game = await make_game(
        db, primary_name="Review Game", enrichment_status=EnrichmentStatus.NEEDS_REVIEW
    )
    review_session = await make_session(
        db, user.discord_id, review_game.id, dt(hours_ago=2), dt(hours_ago=1)
    )

    resp = await authed_client.get("/api/v1/sessions")

    assert resp.status_code == 200
    ids = {row["id"] for row in resp.json()}
    assert ids == {ignored_session.id, review_session.id}


async def test_library_only_includes_accepted_needs_review_game(authed_client, db, user):
    """An accepted NEEDS_REVIEW game's session remains visible under library_only=true."""
    game = await make_game(db, enrichment_status=EnrichmentStatus.NEEDS_REVIEW)
    await make_pref(db, user.discord_id, game.id, is_accepted=True)
    session = await make_session(db, user.discord_id, game.id, dt(hours_ago=2), dt(hours_ago=1))

    resp = await authed_client.get("/api/v1/sessions?library_only=true")

    assert resp.status_code == 200
    ids = {row["id"] for row in resp.json()}
    assert ids == {session.id}
