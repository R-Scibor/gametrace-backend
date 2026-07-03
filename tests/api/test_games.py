import base64
from datetime import date, datetime, timezone
from sqlalchemy import select

from app.models.game import CoverSource, EnrichmentStatus, UserGamePreference
from app.models.session import GameSession, SessionStatus

from tests.factories import (
    dt,
    make_alias,
    make_game,
    make_pref,
    make_session,
    make_user,
)


# ── GET /games — playtime + last_played aggregation ──────────────────────────

async def test_response_includes_playtime_and_last_played(authed_client, db, user):
    game = await make_game(db, "Timed")
    await make_session(db, user.discord_id, game.id, dt(hours_ago=3), dt(hours_ago=2))  # 3600s
    await make_session(db, user.discord_id, game.id, dt(hours_ago=6), dt(hours_ago=4))  # 7200s

    resp = await authed_client.get("/api/v1/games")

    assert resp.status_code == 200
    item = next(g for g in resp.json()["items"] if g["primary_name"] == "Timed")
    assert item["total_seconds"] == 10800
    assert item["last_played"] is not None


async def test_error_session_contributes_zero_playtime_but_sets_last_played(authed_client, db, user):
    game = await make_game(db, "Errored")
    await make_session(
        db, user.discord_id, game.id, dt(hours_ago=2), None,
        status=SessionStatus.ERROR,
    )

    resp = await authed_client.get("/api/v1/games")

    item = next(g for g in resp.json()["items"] if g["primary_name"] == "Errored")
    assert item["total_seconds"] == 0
    assert item["last_played"] is not None


async def test_ongoing_session_counted_live(authed_client, db, user):
    game = await make_game(db, "Live")
    await make_session(
        db, user.discord_id, game.id, dt(hours_ago=2), None,
        status=SessionStatus.ONGOING,
    )

    resp = await authed_client.get("/api/v1/games")

    item = next(g for g in resp.json()["items"] if g["primary_name"] == "Live")
    # ~2h live; allow a wide margin for clock/DB timing
    assert 7000 <= item["total_seconds"] <= 7400


# ── GET /games — facet filters ───────────────────────────────────────────────

async def test_filter_by_developer(authed_client, db, user):
    fromsoft = await make_game(db, "Elden Ring", developers=["FromSoftware"])
    valve = await make_game(db, "Half-Life", developers=["Valve"])
    await make_session(db, user.discord_id, fromsoft.id, dt(hours_ago=3), dt(hours_ago=2))
    await make_session(db, user.discord_id, valve.id, dt(hours_ago=5), dt(hours_ago=4))

    resp = await authed_client.get("/api/v1/games?developer=FromSoftware")

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["primary_name"] == "Elden Ring"


async def test_filter_by_genre_multi_genre_game_matches(authed_client, db, user):
    game = await make_game(db, "RPG Shooter", genres=["RPG", "Shooter"])
    other = await make_game(db, "Pure Puzzle", genres=["Puzzle"])
    await make_session(db, user.discord_id, game.id, dt(hours_ago=3), dt(hours_ago=2))
    await make_session(db, user.discord_id, other.id, dt(hours_ago=5), dt(hours_ago=4))

    resp = await authed_client.get("/api/v1/games?genre=Shooter")

    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["primary_name"] == "RPG Shooter"


async def test_filter_by_genre_is_case_sensitive(authed_client, db, user):
    game = await make_game(db, "Caser", genres=["RPG"])
    await make_session(db, user.discord_id, game.id, dt(hours_ago=3), dt(hours_ago=2))

    resp = await authed_client.get("/api/v1/games?genre=rpg")

    assert resp.status_code == 200
    assert resp.json()["total"] == 0


async def test_filter_by_release_decade(authed_client, db, user):
    in_2010s = await make_game(db, "Twenty-Fifteen", first_release_date=date(2015, 6, 1))
    in_2000s = await make_game(db, "Oh-Five", first_release_date=date(2005, 6, 1))
    no_date = await make_game(db, "Dateless")
    for g in (in_2010s, in_2000s, no_date):
        await make_session(db, user.discord_id, g.id, dt(hours_ago=3), dt(hours_ago=2))

    resp = await authed_client.get("/api/v1/games?release_decade=2010s")

    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["primary_name"] == "Twenty-Fifteen"


async def test_invalid_release_decade_rejected(authed_client, db, user):
    resp = await authed_client.get("/api/v1/games?release_decade=2013s")
    assert resp.status_code == 422


# ── GET /games — sort + order ────────────────────────────────────────────────

async def test_sort_by_playtime_desc(authed_client, db, user):
    low = await make_game(db, "Low")
    high = await make_game(db, "High")
    await make_session(db, user.discord_id, low.id, dt(hours_ago=3), dt(hours_ago=2))    # 3600
    await make_session(db, user.discord_id, high.id, dt(hours_ago=6), dt(hours_ago=2))   # 14400

    resp = await authed_client.get("/api/v1/games?sort=playtime")

    names = [g["primary_name"] for g in resp.json()["items"]]
    assert names == ["High", "Low"]


async def test_sort_by_playtime_asc(authed_client, db, user):
    low = await make_game(db, "Low")
    high = await make_game(db, "High")
    await make_session(db, user.discord_id, low.id, dt(hours_ago=3), dt(hours_ago=2))
    await make_session(db, user.discord_id, high.id, dt(hours_ago=6), dt(hours_ago=2))

    resp = await authed_client.get("/api/v1/games?sort=playtime&order=asc")

    names = [g["primary_name"] for g in resp.json()["items"]]
    assert names == ["Low", "High"]


async def test_sort_by_last_played_desc(authed_client, db, user):
    old = await make_game(db, "Old")
    recent = await make_game(db, "Recent")
    await make_session(db, user.discord_id, old.id, dt(hours_ago=50), dt(hours_ago=49))
    await make_session(db, user.discord_id, recent.id, dt(hours_ago=2), dt(hours_ago=1))

    resp = await authed_client.get("/api/v1/games?sort=last_played")

    names = [g["primary_name"] for g in resp.json()["items"]]
    assert names == ["Recent", "Old"]


async def test_tied_playtime_pagination_is_stable(authed_client, db, user):
    # 5 games, all zero playtime (ERROR-only) → all tied; tie-break by name asc.
    for i in range(5):
        g = await make_game(db, f"Tie {i:02d}")
        await make_session(
            db, user.discord_id, g.id, dt(hours_ago=3), None, status=SessionStatus.ERROR
        )

    page1 = await authed_client.get("/api/v1/games?sort=playtime&skip=0&limit=2")
    page2 = await authed_client.get("/api/v1/games?sort=playtime&skip=2&limit=2")

    names1 = [g["primary_name"] for g in page1.json()["items"]]
    names2 = [g["primary_name"] for g in page2.json()["items"]]
    assert names1 == ["Tie 00", "Tie 01"]
    assert names2 == ["Tie 02", "Tie 03"]
    assert set(names1).isdisjoint(names2)


async def test_invalid_sort_rejected(authed_client, db, user):
    resp = await authed_client.get("/api/v1/games?sort=bogus")
    assert resp.status_code == 422


async def test_default_sort_is_name_asc(authed_client, db, user):
    b = await make_game(db, "Bravo")
    a = await make_game(db, "Alpha")
    await make_session(db, user.discord_id, b.id, dt(hours_ago=3), dt(hours_ago=1))
    await make_session(db, user.discord_id, a.id, dt(hours_ago=3), dt(hours_ago=2))

    resp = await authed_client.get("/api/v1/games")

    names = [g["primary_name"] for g in resp.json()["items"]]
    assert names == ["Alpha", "Bravo"]


# ── GET /games ────────────────────────────────────────────────────────────────

async def test_returns_user_games(authed_client, db, user):
    game_a = await make_game(db, "Alpha")
    game_b = await make_game(db, "Beta")
    await make_session(db, user.discord_id, game_a.id, dt(hours_ago=3), dt(hours_ago=2))
    await make_session(db, user.discord_id, game_b.id, dt(hours_ago=5), dt(hours_ago=4))

    resp = await authed_client.get("/api/v1/games")

    assert resp.status_code == 200
    body = resp.json()
    names = {g["primary_name"] for g in body["items"]}
    assert names == {"Alpha", "Beta"}
    assert body["total"] == 2


async def test_excludes_other_users_games(authed_client, db, user):
    other = await make_user(db, discord_id="222222222222222222", username="other")
    game_mine = await make_game(db, "Mine")
    game_theirs = await make_game(db, "Theirs")
    await make_session(db, user.discord_id, game_mine.id, dt(hours_ago=3), dt(hours_ago=2))
    await make_session(db, other.discord_id, game_theirs.id, dt(hours_ago=3), dt(hours_ago=2))

    resp = await authed_client.get("/api/v1/games")

    assert resp.status_code == 200
    names = [g["primary_name"] for g in resp.json()["items"]]
    assert "Mine" in names
    assert "Theirs" not in names


async def test_excludes_ignored_games(authed_client, db, user):
    game_ok = await make_game(db, "Visible")
    game_hidden = await make_game(db, "Hidden")
    await make_session(db, user.discord_id, game_ok.id, dt(hours_ago=3), dt(hours_ago=2))
    await make_session(db, user.discord_id, game_hidden.id, dt(hours_ago=5), dt(hours_ago=4))
    await make_pref(db, user.discord_id, game_hidden.id, is_ignored=True)

    resp = await authed_client.get("/api/v1/games")

    assert resp.status_code == 200
    names = [g["primary_name"] for g in resp.json()["items"]]
    assert "Visible" in names
    assert "Hidden" not in names


async def test_is_ignored_filter_returns_hidden_games_only(authed_client, db, user):
    game_ok = await make_game(db, "Visible")
    game_hidden = await make_game(db, "Hidden")
    await make_session(db, user.discord_id, game_ok.id, dt(hours_ago=3), dt(hours_ago=2))
    await make_session(db, user.discord_id, game_hidden.id, dt(hours_ago=5), dt(hours_ago=4))
    await make_pref(db, user.discord_id, game_hidden.id, is_ignored=True)

    resp = await authed_client.get("/api/v1/games?is_ignored=true")

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["primary_name"] == "Hidden"
    assert body["items"][0]["is_ignored"] is True


async def test_is_ignored_filter_includes_needs_review_stubs(authed_client, db, user):
    game = await make_game(db, "Ignored Stub", EnrichmentStatus.NEEDS_REVIEW)
    await make_session(db, user.discord_id, game.id, dt(hours_ago=3), dt(hours_ago=2))
    await make_pref(db, user.discord_id, game.id, is_ignored=True)

    resp = await authed_client.get("/api/v1/games?is_ignored=true")

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["primary_name"] == "Ignored Stub"


async def test_is_ignored_filter_pagination(authed_client, db, user):
    for i in range(5):
        game = await make_game(db, f"Hidden {i:02d}")
        await make_session(db, user.discord_id, game.id, dt(hours_ago=3), dt(hours_ago=2))
        await make_pref(db, user.discord_id, game.id, is_ignored=True)

    resp = await authed_client.get("/api/v1/games?is_ignored=true&skip=2&limit=2")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 2
    assert body["total"] == 5


async def test_is_ignored_filter_search_q(authed_client, db, user):
    game_a = await make_game(db, "Dark Souls III")
    game_b = await make_game(db, "Elden Ring")
    await make_session(db, user.discord_id, game_a.id, dt(hours_ago=3), dt(hours_ago=2))
    await make_session(db, user.discord_id, game_b.id, dt(hours_ago=5), dt(hours_ago=4))
    await make_pref(db, user.discord_id, game_a.id, is_ignored=True)
    await make_pref(db, user.discord_id, game_b.id, is_ignored=True)

    resp = await authed_client.get("/api/v1/games?is_ignored=true&q=dark")

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["primary_name"] == "Dark Souls III"


async def test_in_library_false_returns_out_of_library_union(authed_client, db, user):
    game_visible = await make_game(db, "Visible")
    game_hidden = await make_game(db, "Hidden")
    game_review = await make_game(db, "Review Stub", EnrichmentStatus.NEEDS_REVIEW)
    game_both = await make_game(db, "Ignored Review", EnrichmentStatus.NEEDS_REVIEW)
    for game in (game_visible, game_hidden, game_review, game_both):
        await make_session(db, user.discord_id, game.id, dt(hours_ago=3), dt(hours_ago=2))
    await make_pref(db, user.discord_id, game_hidden.id, is_ignored=True)
    await make_pref(db, user.discord_id, game_both.id, is_ignored=True)

    resp = await authed_client.get("/api/v1/games?in_library=false")

    assert resp.status_code == 200
    body = resp.json()
    names = {g["primary_name"] for g in body["items"]}
    assert body["total"] == 3
    assert names == {"Hidden", "Review Stub", "Ignored Review"}
    assert "Visible" not in names


async def test_in_library_false_pagination(authed_client, db, user):
    for i in range(4):
        game = await make_game(db, f"Hidden {i:02d}")
        await make_session(db, user.discord_id, game.id, dt(hours_ago=3), dt(hours_ago=2))
        await make_pref(db, user.discord_id, game.id, is_ignored=True)

    resp = await authed_client.get("/api/v1/games?in_library=false&skip=1&limit=2")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 2
    assert body["total"] == 4


async def test_in_library_false_search_q(authed_client, db, user):
    game_hidden = await make_game(db, "Dark Souls III")
    game_review = await make_game(db, "Elden Ring", EnrichmentStatus.NEEDS_REVIEW)
    await make_session(db, user.discord_id, game_hidden.id, dt(hours_ago=3), dt(hours_ago=2))
    await make_session(db, user.discord_id, game_review.id, dt(hours_ago=5), dt(hours_ago=4))
    await make_pref(db, user.discord_id, game_hidden.id, is_ignored=True)

    resp = await authed_client.get("/api/v1/games?in_library=false&q=dark")

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["primary_name"] == "Dark Souls III"


async def test_in_library_false_with_status_needs_review(authed_client, db, user):
    game_hidden = await make_game(db, "Hidden Enriched")
    game_review = await make_game(db, "Review Stub", EnrichmentStatus.NEEDS_REVIEW)
    game_ignored_review = await make_game(db, "Ignored Review", EnrichmentStatus.NEEDS_REVIEW)
    for game in (game_hidden, game_review, game_ignored_review):
        await make_session(db, user.discord_id, game.id, dt(hours_ago=3), dt(hours_ago=2))
    await make_pref(db, user.discord_id, game_hidden.id, is_ignored=True)
    await make_pref(db, user.discord_id, game_ignored_review.id, is_ignored=True)

    resp = await authed_client.get("/api/v1/games?in_library=false&status=NEEDS_REVIEW")

    assert resp.status_code == 200
    body = resp.json()
    names = {g["primary_name"] for g in body["items"]}
    assert body["total"] == 2
    assert names == {"Review Stub", "Ignored Review"}


async def test_needs_review_hidden_from_main_library_until_accepted(authed_client, db, user):
    game = await make_game(db, "Unknown Launcher", EnrichmentStatus.NEEDS_REVIEW)
    await make_session(db, user.discord_id, game.id, dt(hours_ago=3), dt(hours_ago=2))

    resp = await authed_client.get("/api/v1/games")

    assert resp.status_code == 200
    names = [g["primary_name"] for g in resp.json()["items"]]
    assert "Unknown Launcher" not in names


async def test_accepted_needs_review_appears_in_main_library(authed_client, db, user):
    game = await make_game(db, "Indie Stub", EnrichmentStatus.NEEDS_REVIEW)
    await make_session(db, user.discord_id, game.id, dt(hours_ago=3), dt(hours_ago=2))
    await make_pref(db, user.discord_id, game.id, is_accepted=True)

    resp = await authed_client.get("/api/v1/games")

    assert resp.status_code == 200
    names = [g["primary_name"] for g in resp.json()["items"]]
    assert "Indie Stub" in names


async def test_status_filter_needs_review(authed_client, db, user):
    game_pending = await make_game(db, "Pending Game", EnrichmentStatus.PENDING)
    game_review = await make_game(db, "Review Game", EnrichmentStatus.NEEDS_REVIEW)
    await make_session(db, user.discord_id, game_pending.id, dt(hours_ago=3), dt(hours_ago=2))
    await make_session(db, user.discord_id, game_review.id, dt(hours_ago=5), dt(hours_ago=4))

    resp = await authed_client.get("/api/v1/games?status=NEEDS_REVIEW")

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["primary_name"] == "Review Game"


async def test_pagination(authed_client, db, user):
    for i in range(25):
        game = await make_game(db, f"Game {i:02d}")
        await make_session(db, user.discord_id, game.id, dt(hours_ago=3), dt(hours_ago=2))

    resp = await authed_client.get("/api/v1/games?skip=20&limit=10")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 5
    assert body["total"] == 25


async def test_game_with_no_sessions_not_returned(authed_client, db, user):
    _orphan = await make_game(db, "Orphan Game")

    resp = await authed_client.get("/api/v1/games")

    assert resp.status_code == 200
    names = [g["primary_name"] for g in resp.json()["items"]]
    assert "Orphan Game" not in names


async def test_flicker_only_game_absent_from_library(authed_client, db, user):
    """A game whose only sessions are flicker rows must not appear in GET /games."""
    game = await make_game(db, "Flicker Ghost")
    await make_session(db, user.discord_id, game.id, dt(hours_ago=3), dt(hours_ago=2), is_flicker=True)

    resp = await authed_client.get("/api/v1/games")

    assert resp.status_code == 200
    names = [g["primary_name"] for g in resp.json()["items"]]
    assert "Flicker Ghost" not in names


async def test_search_q_filters_by_name(authed_client, db, user):
    game_a = await make_game(db, "Dark Souls III")
    game_b = await make_game(db, "Elden Ring")
    await make_session(db, user.discord_id, game_a.id, dt(hours_ago=3), dt(hours_ago=2))
    await make_session(db, user.discord_id, game_b.id, dt(hours_ago=5), dt(hours_ago=4))

    resp = await authed_client.get("/api/v1/games?q=dark")

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["primary_name"] == "Dark Souls III"


async def test_search_q_case_insensitive(authed_client, db, user):
    game = await make_game(db, "Hollow Knight")
    await make_session(db, user.discord_id, game.id, dt(hours_ago=3), dt(hours_ago=2))

    resp = await authed_client.get("/api/v1/games?q=HOLLOW")

    assert resp.status_code == 200
    assert resp.json()["total"] == 1


async def test_search_q_no_match_returns_empty(authed_client, db, user):
    game = await make_game(db, "Celeste")
    await make_session(db, user.discord_id, game.id, dt(hours_ago=3), dt(hours_ago=2))

    resp = await authed_client.get("/api/v1/games?q=zzznomatch")

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 0
    assert body["items"] == []


async def test_total_reflects_full_count_not_page(authed_client, db, user):
    for i in range(5):
        game = await make_game(db, f"Title {i:02d}")
        await make_session(db, user.discord_id, game.id, dt(hours_ago=3), dt(hours_ago=2))

    resp = await authed_client.get("/api/v1/games?limit=2")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 2
    assert body["total"] == 5


# ── GET /games/{id}/sessions ──────────────────────────────────────────────────

async def test_returns_sessions_for_game(authed_client, db, user):
    game = await make_game(db)
    s1 = await make_session(db, user.discord_id, game.id, dt(hours_ago=10), dt(hours_ago=9))
    s2 = await make_session(db, user.discord_id, game.id, dt(hours_ago=5), dt(hours_ago=4))
    s3 = await make_session(db, user.discord_id, game.id, dt(hours_ago=2), dt(hours_ago=1))

    resp = await authed_client.get(f"/api/v1/games/{game.id}/sessions")

    assert resp.status_code == 200
    ids = [s["id"] for s in resp.json()]
    assert ids == [s3.id, s2.id, s1.id]  # newest first


async def test_ignored_game_still_returns_sessions(authed_client, db, user):
    game = await make_game(db)
    session = await make_session(db, user.discord_id, game.id, dt(hours_ago=3), dt(hours_ago=2))
    await make_pref(db, user.discord_id, game.id, is_ignored=True)

    resp = await authed_client.get(f"/api/v1/games/{game.id}/sessions")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["id"] == session.id


async def test_excludes_soft_deleted_sessions(authed_client, db, user):
    game = await make_game(db)
    visible = await make_session(db, user.discord_id, game.id, dt(hours_ago=5), dt(hours_ago=4))
    deleted = await make_session(
        db, user.discord_id, game.id, dt(hours_ago=3), dt(hours_ago=2),
        deleted_at=datetime.now(timezone.utc),
    )

    resp = await authed_client.get(f"/api/v1/games/{game.id}/sessions")

    assert resp.status_code == 200
    ids = [s["id"] for s in resp.json()]
    assert visible.id in ids
    assert deleted.id not in ids


# ── POST /games/{id}/merge/{target_id} ───────────────────────────────────────

async def test_merge_happy_path(authed_client, db, user):
    source = await make_game(db, "Source Game")
    target = await make_game(db, "Target Game")
    from app.models.game import Game
    s = await make_session(db, user.discord_id, source.id, dt(hours_ago=3), dt(hours_ago=2))

    resp = await authed_client.post(f"/api/v1/games/{source.id}/merge/{target.id}")

    assert resp.status_code == 204
    deleted = await db.get(Game, source.id)
    assert deleted is None
    await db.refresh(s)
    assert s.game_id == target.id


async def test_aliases_reassigned(authed_client, db, user):
    source = await make_game(db, "Source")
    target = await make_game(db, "Target")
    alias = await make_alias(db, source.id, "source.exe")

    await authed_client.post(f"/api/v1/games/{source.id}/merge/{target.id}")

    await db.refresh(alias)
    assert alias.game_id == target.id


async def test_user_preference_conflict_resolved(authed_client, db, user):
    """User has a pref for both games — source pref is dropped, no UNIQUE violation."""
    source = await make_game(db, "Source")
    target = await make_game(db, "Target")
    await make_pref(db, user.discord_id, source.id, is_ignored=True)
    await make_pref(db, user.discord_id, target.id, is_ignored=False)

    resp = await authed_client.post(f"/api/v1/games/{source.id}/merge/{target.id}")

    assert resp.status_code == 204
    result = await db.execute(
        select(UserGamePreference).where(UserGamePreference.game_id == target.id)
    )
    assert len(result.scalars().all()) == 1


async def test_merge_self_returns_400(authed_client, db, user):
    game = await make_game(db)

    resp = await authed_client.post(f"/api/v1/games/{game.id}/merge/{game.id}")

    assert resp.status_code == 400


async def test_merge_source_not_found(authed_client, db, user):
    target = await make_game(db)

    resp = await authed_client.post(f"/api/v1/games/99999/merge/{target.id}")

    assert resp.status_code == 404


async def test_merge_target_not_found(authed_client, db, user):
    source = await make_game(db)

    resp = await authed_client.post(f"/api/v1/games/{source.id}/merge/99999")

    assert resp.status_code == 404


# ── PUT /games/{id}/cover (disabled) ──────────────────────────────────────────

async def test_cover_upload_disabled(authed_client, db, user):
    game = await make_game(db)
    img_b64 = base64.b64encode(b"fake_image_data").decode()

    resp = await authed_client.put(
        f"/api/v1/games/{game.id}/cover",
        json={"image_base64": img_b64, "extension": "jpg"},
    )

    assert resp.status_code == 403
    await db.refresh(game)
    assert game.cover_source == CoverSource.EXTERNAL
    assert game.cover_image_url is None


async def test_cover_upload_disabled_unknown_game(authed_client, db, user):
    img_b64 = base64.b64encode(b"data").decode()

    resp = await authed_client.put(
        "/api/v1/games/99999/cover",
        json={"image_base64": img_b64, "extension": "jpg"},
    )

    assert resp.status_code == 403
