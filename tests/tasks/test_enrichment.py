"""
tests/tasks/test_enrichment.py

Phase 3 — enrichment worker logic.

Async tests call _run_enrichment() directly (await) with a mocked DB engine and
mocked HTTP helpers. Sync tests call enrich_game.run() to test the Celery task's
retry behaviour — sync because enrich_game calls asyncio.run(), which cannot be
nested inside a running event loop.
"""
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from celery.exceptions import Retry

from app.models.game import CoverSource, EnrichmentStatus, Game
from app.tasks.enrichment import IGDBResult, _RateLimited, _run_enrichment, enrich_game


def _igdb_result(
    cover_url: str | None = None,
    confidence: float = 0.0,
    genres: list[str] | None = None,
    themes: list[str] | None = None,
    developers: list[str] | None = None,
    publishers: list[str] | None = None,
    first_release_date: date | None = None,
) -> IGDBResult:
    return IGDBResult(
        cover_url=cover_url,
        confidence=confidence,
        genres=genres or [],
        themes=themes or [],
        developers=developers or [],
        publishers=publishers or [],
        first_release_date=first_release_date,
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
               return_value=("1145360", "http://steam-cover.jpg")):

        status, cover, ext_id = await _run_enrichment(1)

    assert status == EnrichmentStatus.ENRICHED
    assert cover == "http://steam-cover.jpg"
    assert ext_id == "1145360"
    assert game.cover_image_url == "http://steam-cover.jpg"


async def test_igdb_and_steam_miss():
    """Neither IGDB nor Steam matches → NEEDS_REVIEW."""
    game = _game_mock("Some Obscure Game")
    p_engine, p_sm, _ = _db_patches(game)

    with p_engine, p_sm, \
         patch("app.tasks.enrichment._igdb_search",
               return_value=_igdb_result(None, 0.40)), \
         patch("app.tasks.enrichment._steam_search", return_value=(None, None)):

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


async def test_igdb_metadata_skipped_when_cover_custom():
    """cover_source=CUSTOM → status updated, but metadata + cover untouched."""
    original_cover = "http://my-custom.jpg"
    game = _game_mock("Cyberpunk 2077", cover_source=CoverSource.CUSTOM, cover_url=original_cover)
    game.genres = ["preexisting"]
    game.themes = ["preexisting"]
    game.developers = ["preexisting"]
    game.publishers = ["preexisting"]
    game.first_release_date = date(1999, 1, 1)
    p_engine, p_sm, _ = _db_patches(game)

    result = _igdb_result(
        cover_url="http://igdb-cover.jpg",
        confidence=0.95,
        genres=["RPG"],
        themes=["Sci-Fi"],
        developers=["CDPR"],
        publishers=["CDP"],
        first_release_date=date(2020, 12, 10),
    )

    with p_engine, p_sm, \
         patch("app.tasks.enrichment._igdb_search", return_value=result):

        status, _, _ = await _run_enrichment(1)

    assert status == EnrichmentStatus.ENRICHED
    assert game.cover_image_url == original_cover
    assert game.genres == ["preexisting"]
    assert game.themes == ["preexisting"]
    assert game.developers == ["preexisting"]
    assert game.publishers == ["preexisting"]
    assert game.first_release_date == date(1999, 1, 1)


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
               return_value=("1145360", "http://steam-cover.jpg")):

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

    with patch("app.tasks.enrichment.httpx.Client", return_value=fake_client), \
         patch("app.tasks.enrichment.get_igdb_token", return_value="token"), \
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
