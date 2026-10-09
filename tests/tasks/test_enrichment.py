"""
tests/tasks/test_enrichment.py

Phase 3 — enrichment worker logic.

Async tests call _run_enrichment() directly (await) with a mocked DB engine and
mocked HTTP helpers. Sync tests call enrich_game.run() to test the Celery task's
retry behaviour — sync because enrich_game calls asyncio.run(), which cannot be
nested inside a running event loop.
"""
import json
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from celery.exceptions import Retry

from app.models.game import CoverSource, EnrichmentStatus, Game
from app.tasks.enrichment import (
    IGDBResult,
    _RateLimited,
    _run_backfill,
    _run_enrichment,
    enrich_game,
)


@pytest.fixture(autouse=True)
def _mock_sync_review_preferences():
    with patch("app.tasks.enrichment.sync_review_preferences", new_callable=AsyncMock), \
         patch("app.services.game_matching.sync_review_preferences", new_callable=AsyncMock):
        yield


def _igdb_result(
    cover_url: str | None = None,
    confidence: float = 0.0,
    genres: list[str] | None = None,
    themes: list[str] | None = None,
    developers: list[str] | None = None,
    publishers: list[str] | None = None,
    first_release_date: date | None = None,
    *,
    name: str | None = "Canonical Title",
    igdb_id: int | None = 42,
) -> IGDBResult:
    return IGDBResult(
        cover_url=cover_url,
        confidence=confidence,
        genres=genres or [],
        themes=themes or [],
        developers=developers or [],
        publishers=publishers or [],
        first_release_date=first_release_date,
        name=name,
        igdb_id=igdb_id,
    )


# ── DB layer mock helpers ─────────────────────────────────────────────────────

def _game_mock(
    name: str = "Test Game",
    cover_source: CoverSource = CoverSource.EXTERNAL,
    cover_url: str | None = None,
) -> MagicMock:
    g = MagicMock(spec=Game)
    g.primary_name = name
    g.cover_source = cover_source
    g.cover_image_url = cover_url
    g.enrichment_status = EnrichmentStatus.PENDING
    g.external_api_id = None
    g.genres = []
    g.themes = []
    g.developers = []
    g.publishers = []
    g.first_release_date = None
    return g


def _make_mock_session(game: MagicMock) -> MagicMock:
    session = AsyncMock()
    session.get.return_value = game
    result = MagicMock()
    result.scalar_one_or_none.return_value = game
    session.execute = AsyncMock(return_value=result)
    nested = AsyncMock()
    nested.__aenter__ = AsyncMock(return_value=nested)
    nested.__aexit__ = AsyncMock(return_value=False)
    session.begin_nested = MagicMock(return_value=nested)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    return session


def _db_patches(game: MagicMock):
    mock_session = _make_mock_session(game)
    mock_factory = MagicMock(return_value=mock_session)
    mock_engine = MagicMock()
    mock_engine.dispose = AsyncMock()
    return (
        patch("app.tasks.enrichment.create_async_engine", return_value=mock_engine),
        patch("app.tasks.enrichment.async_sessionmaker", return_value=mock_factory),
        mock_session,
    )


# ── _run_enrichment: IGDB path ────────────────────────────────────────────────

async def test_igdb_high_confidence():
    game = _game_mock("Cyberpunk 2077")
    p_engine, p_sm, mock_session = _db_patches(game)

    with p_engine, p_sm, \
         patch("app.tasks.enrichment._igdb_search",
               return_value=_igdb_result("http://cover.jpg", 0.95)), \
         patch("app.tasks.enrichment._steam_search") as mock_steam:

        status, cover, ext_id = await _run_enrichment(1)

    assert status == EnrichmentStatus.ENRICHED
    assert cover == "http://cover.jpg"
    mock_steam.assert_not_called()
    assert game.enrichment_status == EnrichmentStatus.ENRICHED
    assert game.cover_image_url == "http://cover.jpg"
    assert game.external_api_id == "igdb:42"
    assert game.primary_name == "Canonical Title"


async def test_igdb_at_threshold_passes():
    """Confidence exactly 0.85 should pass (>= threshold)."""
    game = _game_mock("Hades")
    p_engine, p_sm, _ = _db_patches(game)

    with p_engine, p_sm, \
         patch("app.tasks.enrichment._igdb_search",
               return_value=_igdb_result("http://cover.jpg", 0.85)):

        status, cover, _ = await _run_enrichment(1)

    assert status == EnrichmentStatus.ENRICHED
    assert cover == "http://cover.jpg"


async def test_igdb_below_threshold_tries_steam():
    """IGDB confidence < 0.85 → fall through to Steam; Steam match → ENRICHED."""
    game = _game_mock("Hollow Knight")
    p_engine, p_sm, _ = _db_patches(game)

    with p_engine, p_sm, \
         patch("app.tasks.enrichment._igdb_search",
               return_value=_igdb_result(None, 0.84)), \
         patch("app.tasks.enrichment._steam_search",
               return_value=(1145360, "http://steam-cover.jpg", "Hollow Knight")):

        status, cover, ext_id = await _run_enrichment(1)

    assert status == EnrichmentStatus.ENRICHED
    assert cover == "http://steam-cover.jpg"
    assert ext_id == "steam:1145360"
    assert game.primary_name == "Hollow Knight"
    assert game.cover_image_url == "http://steam-cover.jpg"


async def test_igdb_and_steam_miss():
    """Neither IGDB nor Steam matches → NEEDS_REVIEW."""
    game = _game_mock("Some Obscure Game")
    p_engine, p_sm, _ = _db_patches(game)

    with p_engine, p_sm, \
         patch("app.tasks.enrichment._igdb_search",
               return_value=_igdb_result(None, 0.40)), \
         patch("app.tasks.enrichment._steam_search", return_value=(None, None, None)):

        status, cover, ext_id = await _run_enrichment(1)

    assert status == EnrichmentStatus.NEEDS_REVIEW
    assert cover is None
    assert ext_id is None
    assert game.enrichment_status == EnrichmentStatus.NEEDS_REVIEW


async def test_custom_cover_not_overwritten():
    """IGDB returns a cover but cover_source=CUSTOM → cover_image_url must not change."""
    original_cover = "http://my-custom.jpg"
    game = _game_mock("Cyberpunk 2077", cover_source=CoverSource.CUSTOM, cover_url=original_cover)
    p_engine, p_sm, _ = _db_patches(game)

    with p_engine, p_sm, \
         patch("app.tasks.enrichment._igdb_search",
               return_value=_igdb_result("http://igdb-cover.jpg", 0.95)):

        status, _, _ = await _run_enrichment(1)

    assert status == EnrichmentStatus.ENRICHED
    assert game.cover_image_url == original_cover  # unchanged


async def test_igdb_writes_metadata():
    """High-confidence IGDB result with metadata → fields written to game row."""
    game = _game_mock("Cyberpunk 2077")
    p_engine, p_sm, _ = _db_patches(game)

    result = _igdb_result(
        cover_url="http://cover.jpg",
        confidence=0.95,
        genres=["RPG", "Shooter"],
        themes=["Sci-Fi", "Action"],
        developers=["CD Projekt Red"],
        publishers=["CD Projekt"],
        first_release_date=date(2020, 12, 10),
    )

    with p_engine, p_sm, \
         patch("app.tasks.enrichment._igdb_search", return_value=result):

        status, _, _ = await _run_enrichment(1)

    assert status == EnrichmentStatus.ENRICHED
    assert game.genres == ["RPG", "Shooter"]
    assert game.themes == ["Sci-Fi", "Action"]
    assert game.developers == ["CD Projekt Red"]
    assert game.publishers == ["CD Projekt"]
    assert game.first_release_date == date(2020, 12, 10)


async def test_igdb_empty_metadata():
    """High-confidence IGDB result with empty metadata → empty lists / None release date."""
    game = _game_mock("Cyberpunk 2077")
    p_engine, p_sm, _ = _db_patches(game)

    with p_engine, p_sm, \
         patch("app.tasks.enrichment._igdb_search",
               return_value=_igdb_result("http://cover.jpg", 0.95)):

        status, _, _ = await _run_enrichment(1)

    assert status == EnrichmentStatus.ENRICHED
    assert game.genres == []
    assert game.themes == []
    assert game.developers == []
    assert game.publishers == []
    assert game.first_release_date is None


async def test_igdb_custom_cover_still_writes_metadata():
    """CUSTOM keeps the cover URL and cover_source. Metadata, id, and title write."""
    original_cover = "http://my-custom.jpg"
    game = _game_mock("game.exe", cover_source=CoverSource.CUSTOM, cover_url=original_cover)
    game.genres = ["preexisting"]
    p_engine, p_sm, _ = _db_patches(game)
    result = _igdb_result(
        cover_url="http://igdb-cover.jpg",
        confidence=0.95,
        genres=["RPG"],
        themes=["Sci-Fi"],
        developers=["CDPR"],
        publishers=["CDP"],
        first_release_date=date(2020, 12, 10),
        name="Cyberpunk 2077",
        igdb_id=1877,
    )

    with p_engine, p_sm, \
         patch("app.tasks.enrichment._igdb_search", return_value=result):
        status, _, ext_id = await _run_enrichment(1)

    assert status == EnrichmentStatus.ENRICHED
    assert ext_id == "igdb:1877"
    assert game.cover_image_url == original_cover
    assert game.cover_source == CoverSource.CUSTOM
    assert game.primary_name == "Cyberpunk 2077"
    assert game.genres == ["RPG"]
    assert game.first_release_date == date(2020, 12, 10)


async def test_igdb_hit_without_an_id_falls_through_to_steam():
    game = _game_mock("Hollow Knight")
    p_engine, p_sm, _ = _db_patches(game)
    with p_engine, p_sm, \
         patch("app.tasks.enrichment._igdb_search",
               return_value=_igdb_result("http://igdb.jpg", 0.95, name=None, igdb_id=None)), \
         patch("app.tasks.enrichment._steam_search",
               return_value=(1145360, "http://steam-cover.jpg", "Hollow Knight")):
        status, cover, ext_id = await _run_enrichment(1)

    assert status == EnrichmentStatus.ENRICHED
    assert cover == "http://steam-cover.jpg"
    assert ext_id == "steam:1145360"
    assert game.external_api_id == "steam:1145360"


async def test_steam_fallback_does_not_touch_metadata():
    """Steam fallback path → existing metadata stays put (Steam never populates it)."""
    game = _game_mock("Hollow Knight")
    game.genres = ["existing"]
    game.themes = ["existing"]
    game.developers = ["existing"]
    game.publishers = ["existing"]
    game.first_release_date = date(2017, 2, 24)
    p_engine, p_sm, _ = _db_patches(game)

    with p_engine, p_sm, \
         patch("app.tasks.enrichment._igdb_search",
               return_value=_igdb_result(None, 0.40)), \
         patch("app.tasks.enrichment._steam_search",
               return_value=(1145360, "http://steam-cover.jpg", "Hollow Knight")):

        status, _, _ = await _run_enrichment(1)

    assert status == EnrichmentStatus.ENRICHED
    assert game.cover_image_url == "http://steam-cover.jpg"
    assert game.genres == ["existing"]
    assert game.themes == ["existing"]
    assert game.developers == ["existing"]
    assert game.publishers == ["existing"]
    assert game.first_release_date == date(2017, 2, 24)


def test_igdb_search_parses_metadata_response():
    """Unit-level: _igdb_search parses genres, themes, devs, pubs, first_release_date."""
    from app.tasks import enrichment as enr

    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = [
        {
            "id": 1877,
            "name": "Cyberpunk 2077",
            "cover": {"url": "//images.igdb.com/t_thumb/abc.jpg"},
            "alternative_names": [],
            "genres": [{"name": "RPG"}, {"name": "Shooter"}, {}],
            "themes": [{"name": "Sci-Fi"}],
            "involved_companies": [
                {"company": {"name": "CD Projekt Red"}, "developer": True, "publisher": False},
                {"company": {"name": "CD Projekt"}, "developer": False, "publisher": True},
                {"company": {"name": "Both Co"}, "developer": True, "publisher": True},
                {"company": {"name": "Ignored"}, "developer": False, "publisher": False},
            ],
            "first_release_date": 1577836800,  # 2020-01-01 UTC
        }
    ]
    fake_client = MagicMock()
    fake_client.__enter__ = MagicMock(return_value=fake_client)
    fake_client.__exit__ = MagicMock(return_value=None)
    fake_client.post.return_value = fake_resp

    with patch("app.services.game_matching.httpx.Client", return_value=fake_client), \
         patch("app.services.game_matching.get_igdb_token", return_value="token"), \
         patch.object(enr.settings, "igdb_client_id", "cid"), \
         patch.object(enr.settings, "igdb_client_secret", "secret"):

        result = enr._igdb_search("Cyberpunk 2077")

    assert result.confidence >= 0.95
    assert result.cover_url == "https://images.igdb.com/t_cover_big/abc.jpg"
    assert result.genres == ["RPG", "Shooter"]
    assert result.themes == ["Sci-Fi"]
    assert result.developers == ["CD Projekt Red", "Both Co"]
    assert result.publishers == ["CD Projekt", "Both Co"]
    assert result.first_release_date == date.fromtimestamp(1577836800)
    assert result.name == "Cyberpunk 2077"
    assert result.igdb_id == 1877


def test_igdb_search_parses_parent_rollup():
    """Unit-level: _igdb_search rolls publishers up to parent and aliases Cognosphere → miHoYo."""
    from app.tasks import enrichment as enr

    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = [
        {
            "id": 217958,
            "name": "Honkai: Star Rail",
            "cover": {"url": "//images.igdb.com/t_thumb/hsr.jpg"},
            "alternative_names": [],
            "genres": [{"name": "RPG"}],
            "themes": [{"name": "Sci-Fi"}],
            "involved_companies": [
                # Developer with a parent — parent NOT applied to developers
                {
                    "company": {"name": "Child Dev Studio", "parent": {"name": "Parent Group"}},
                    "developer": True,
                    "publisher": False,
                },
                # Publisher with a parent — should roll up to parent name
                {
                    "company": {"name": "Sub Label", "parent": {"name": "Parent Group"}},
                    "developer": False,
                    "publisher": True,
                },
                # Cognosphere as publisher (no parent) — alias fires → miHoYo
                {
                    "company": {"name": "Cognosphere", "parent": None},
                    "developer": False,
                    "publisher": True,
                },
                # HoYoverse directly (no parent) — also aliases to miHoYo, deduped
                {
                    "company": {"name": "HoYoverse", "parent": None},
                    "developer": False,
                    "publisher": True,
                },
            ],
            "first_release_date": 1682899200,  # 2023-05-01 UTC
        }
    ]
    fake_client = MagicMock()
    fake_client.__enter__ = MagicMock(return_value=fake_client)
    fake_client.__exit__ = MagicMock(return_value=None)
    fake_client.post.return_value = fake_resp

    with patch("app.services.game_matching.httpx.Client", return_value=fake_client), \
         patch("app.services.game_matching.get_igdb_token", return_value="token"), \
         patch.object(enr.settings, "igdb_client_id", "cid"), \
         patch.object(enr.settings, "igdb_client_secret", "secret"):

        result = enr._igdb_search("Honkai Star Rail")

    # Developers stay raw — no parent rollup applied
    assert result.developers == ["Child Dev Studio"]
    # Publishers: Sub Label → Parent Group; Cognosphere/HoYoverse → miHoYo (alias to IGDB root); deduped
    assert result.publishers == ["Parent Group", "miHoYo"]
    assert result.name == "Honkai: Star Rail"
    assert result.igdb_id == 217958


async def test_game_not_found_raises():
    """LookupError is raised (and logged by Celery task) when game_id not in DB."""
    game = None
    p_engine, p_sm, mock_session = _db_patches(game)
    mock_session.get.return_value = None

    with p_engine, p_sm, pytest.raises(LookupError):
        await _run_enrichment(99999)


# ── enrich_game Celery task: retry behaviour ──────────────────────────────────
# These are *sync* tests because enrich_game calls asyncio.run() internally,
# which cannot run inside an already-running event loop.

def test_igdb_rate_limited_triggers_retry():
    # enrich_game.run(game_id) calls the bound function with self=enrich_game.
    # Patch `retry` on the underlying resolved task object to capture the call.
    resolved = enrich_game._get_current_object()
    enrich_game.request.retries = 0

    # Patch _run_enrichment as AsyncMock so asyncio.run() actually awaits it
    # (avoiding an unawaited-coroutine warning from patching asyncio directly).
    with patch.object(resolved, "retry", side_effect=Retry()) as mock_retry, \
         patch("app.tasks.enrichment._run_enrichment",
               new_callable=AsyncMock, side_effect=_RateLimited("IGDB")):

        with pytest.raises(Retry):
            enrich_game.run(1)

    mock_retry.assert_called_once()
    assert mock_retry.call_args.kwargs["countdown"] == 60  # 2^0 * 60


def test_steam_rate_limited_triggers_retry():
    resolved = enrich_game._get_current_object()
    enrich_game.request.retries = 1

    with patch.object(resolved, "retry", side_effect=Retry()) as mock_retry, \
         patch("app.tasks.enrichment._run_enrichment",
               new_callable=AsyncMock, side_effect=_RateLimited("Steam")):

        with pytest.raises(Retry):
            enrich_game.run(1)

    mock_retry.assert_called_once()
    assert mock_retry.call_args.kwargs["countdown"] == 120  # 2^1 * 60
    # Reset to avoid cross-test pollution
    enrich_game.request.retries = 0


# ── backfill_metadata ─────────────────────────────────────────────────────────

def _backfill_session_mock(execute_results: list[list[int]]) -> tuple[MagicMock, AsyncMock]:
    """Build a mock async session whose db.execute() returns scalars().all() = each
    list in execute_results in turn."""
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)

    def make_result(ids: list[int]) -> MagicMock:
        scalars = MagicMock()
        scalars.all.return_value = ids
        result = MagicMock()
        result.scalars.return_value = scalars
        return result

    session.execute = AsyncMock(side_effect=[make_result(ids) for ids in execute_results])
    factory = MagicMock(return_value=session)
    return factory, session


def _backfill_engine_patches(factory: MagicMock):
    mock_engine = MagicMock()
    mock_engine.dispose = AsyncMock()
    return (
        patch("app.tasks.enrichment.create_async_engine", return_value=mock_engine),
        patch("app.tasks.enrichment.async_sessionmaker", return_value=factory),
    )


async def test_backfill_only_queues_empty_genre_games():
    """SELECT filters at the SQL layer; mock returns only the 2 empty-genre ENRICHED rows."""
    factory, session = _backfill_session_mock([[10, 20]])
    p_engine, p_sm = _backfill_engine_patches(factory)

    with p_engine, p_sm, \
         patch("app.tasks.enrichment.enrich_game.apply_async") as mock_apply:

        queued = await _run_backfill(batch_size=500)

    assert queued == 2
    assert mock_apply.call_count == 2
    assert mock_apply.call_args_list[0].kwargs == {
        "args": [10],
        "task_id": "enrich_game_10",
    }
    assert mock_apply.call_args_list[1].kwargs == {
        "args": [20],
        "task_id": "enrich_game_20",
    }


async def test_backfill_chunks_correctly():
    """First chunk fills batch_size → loop continues; second chunk empty → stop."""
    factory, session = _backfill_session_mock([[1, 2, 3], []])
    p_engine, p_sm = _backfill_engine_patches(factory)

    with p_engine, p_sm, \
         patch("app.tasks.enrichment.enrich_game.apply_async") as mock_apply:

        queued = await _run_backfill(batch_size=3)

    assert queued == 3
    assert session.execute.call_count == 2
    assert mock_apply.call_count == 3


async def test_backfill_full_omits_genre_predicate():
    """full=True must omit the jsonb_array_length predicate from the SELECT."""
    factory, session = _backfill_session_mock([[]])
    p_engine, p_sm = _backfill_engine_patches(factory)

    with p_engine, p_sm, \
         patch("app.tasks.enrichment.enrich_game.apply_async"):

        await _run_backfill(batch_size=500, full=True)

    stmt = session.execute.call_args_list[0].args[0]
    assert "jsonb_array_length" not in str(stmt)


async def test_backfill_default_keeps_genre_predicate():
    """Default (full=False) must keep the jsonb_array_length predicate."""
    factory, session = _backfill_session_mock([[]])
    p_engine, p_sm = _backfill_engine_patches(factory)

    with p_engine, p_sm, \
         patch("app.tasks.enrichment.enrich_game.apply_async"):

        await _run_backfill(batch_size=500)

    stmt = session.execute.call_args_list[0].args[0]
    assert "jsonb_array_length" in str(stmt)


def test_steam_search_returns_int_id_cover_and_name():
    from app.tasks.enrichment import _steam_search

    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = {
        "items": [
            {"id": "0", "name": "Not A Game"},
            {"id": 1145360, "name": "Hollow Knight"},
        ]
    }
    fake_resp.raise_for_status.return_value = None
    fake_client = MagicMock()
    fake_client.__enter__ = MagicMock(return_value=fake_client)
    fake_client.__exit__ = MagicMock(return_value=False)
    fake_client.get.return_value = fake_resp

    with patch("app.tasks.enrichment.httpx.Client", return_value=fake_client):
        app_id, cover, name = _steam_search("Hollow Knight")

    assert app_id == 1145360
    assert name == "Hollow Knight"
    assert cover == "https://cdn.akamai.steamstatic.com/steam/apps/1145360/library_600x900.jpg"


def test_steam_search_miss_is_a_triple_of_nones():
    from app.tasks.enrichment import _steam_search

    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = {"items": [{"id": None, "name": "Nope"}]}
    fake_resp.raise_for_status.return_value = None
    fake_client = MagicMock()
    fake_client.__enter__ = MagicMock(return_value=fake_client)
    fake_client.__exit__ = MagicMock(return_value=False)
    fake_client.get.return_value = fake_resp

    with patch("app.tasks.enrichment.httpx.Client", return_value=fake_client):
        assert _steam_search("Nope") == (None, None, None)


def _http_status(code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://example.test")
    response = httpx.Response(code, request=request)
    return httpx.HTTPStatusError(str(code), request=request, response=response)


@pytest.mark.parametrize(
    "exc",
    [
        _RateLimited("IGDB"),
        httpx.TimeoutException("slow"),
        httpx.ConnectError("down"),
        _http_status(401),
        _http_status(429),
        _http_status(500),
        _http_status(503),
    ],
)
def test_lookup_kind_retries_transport_auth_and_5xx(exc):
    from app.tasks.enrichment import _lookup_kind

    assert _lookup_kind(exc) == "retry"


@pytest.mark.parametrize(
    "exc",
    [
        _http_status(400),
        _http_status(404),
        json.JSONDecodeError("bad", "doc", 0),
    ],
)
def test_lookup_kind_unanswered_is_other_4xx_and_bad_json(exc):
    from app.tasks.enrichment import _lookup_kind

    assert _lookup_kind(exc) == "unanswered"


@pytest.mark.parametrize(
    "exc",
    [ValueError("shape"), KeyError("name"), TypeError("shape"), RuntimeError("bug")],
)
def test_lookup_kind_other_errors_are_unexpected(exc):
    from app.tasks.enrichment import _lookup_kind

    assert _lookup_kind(exc) == "unexpected"
