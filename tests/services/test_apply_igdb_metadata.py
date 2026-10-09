"""apply_igdb_metadata flags. The session is the test savepoint session."""

from datetime import date
from unittest.mock import AsyncMock, patch

from app.models.game import CoverSource, EnrichmentStatus
from app.services.game_matching import IGDBResult, apply_igdb_metadata
from tests.factories import make_game

_META = IGDBResult(
    cover_url="https://images.igdb.com/cover.jpg",
    confidence=1.0,
    genres=["RPG"],
    themes=["Action"],
    developers=["Supergiant Games"],
    publishers=["Supergiant Games"],
    first_release_date=date(2020, 9, 17),
    name="Hades",
    igdb_id=1234,
)
_NULL_COVER = _META._replace(cover_url=None)


async def test_replace_identity_sets_prefixed_id_and_title(db):
    game = await make_game(db, "hades.exe", enrichment_status=EnrichmentStatus.NEEDS_REVIEW)
    await apply_igdb_metadata(
        db, game, "Hades", _META,
        igdb_id=1234, replace_identity=True, clear_cover_on_null=True,
    )
    assert game.external_api_id == "igdb:1234"
    assert game.primary_name == "Hades"
    assert game.enrichment_status == EnrichmentStatus.ENRICHED
    assert game.genres == ["RPG"]
    assert game.cover_image_url == _META.cover_url
    assert game.cover_source == CoverSource.EXTERNAL


async def test_replace_identity_false_leaves_id_and_title(db):
    game = await make_game(db, "Keep Me", enrichment_status=EnrichmentStatus.ENRICHED)
    game.external_api_id = "steam:1"
    await db.flush()
    await apply_igdb_metadata(
        db, game, "Hades", _META,
        igdb_id=1234, replace_identity=False, clear_cover_on_null=False,
    )
    assert game.external_api_id == "steam:1"
    assert game.primary_name == "Keep Me"
    assert game.genres == ["RPG"]
    assert game.first_release_date == date(2020, 9, 17)


async def test_custom_cover_keeps_url_and_source_while_metadata_writes(db):
    game = await make_game(db, "hades.exe", enrichment_status=EnrichmentStatus.NEEDS_REVIEW)
    game.cover_source = CoverSource.CUSTOM
    game.cover_image_url = "/covers/1.jpg"
    await db.flush()
    await apply_igdb_metadata(
        db, game, "Hades", _META,
        igdb_id=1234, replace_identity=True, clear_cover_on_null=True,
    )
    assert game.cover_image_url == "/covers/1.jpg"
    assert game.cover_source == CoverSource.CUSTOM
    assert game.external_api_id == "igdb:1234"
    assert game.genres == ["RPG"]


async def test_worker_flag_does_not_clear_a_cover_when_the_url_is_null(db):
    game = await make_game(db, "hades.exe")
    game.cover_image_url = "https://already.example/cover.jpg"
    game.cover_source = CoverSource.EXTERNAL
    await db.flush()
    await apply_igdb_metadata(
        db, game, "Hades", _NULL_COVER,
        igdb_id=1234, replace_identity=True, clear_cover_on_null=False,
    )
    assert game.cover_image_url == "https://already.example/cover.jpg"
    assert game.external_api_id == "igdb:1234"


async def test_admin_flag_clears_a_non_custom_cover_when_the_url_is_null(db):
    game = await make_game(db, "hades.exe")
    game.cover_image_url = "https://already.example/cover.jpg"
    game.cover_source = CoverSource.EXTERNAL
    await db.flush()
    await apply_igdb_metadata(
        db, game, "Hades", _NULL_COVER,
        igdb_id=1234, replace_identity=True, clear_cover_on_null=True,
    )
    assert game.cover_image_url is None
    assert game.cover_source == CoverSource.EXTERNAL


async def test_apply_always_syncs_review_preferences(db):
    game = await make_game(db, "hades.exe", enrichment_status=EnrichmentStatus.ENRICHED)
    game.external_api_id = "igdb:1234"
    await db.flush()
    with patch(
        "app.services.game_matching.sync_review_preferences",
        new_callable=AsyncMock,
    ) as sync:
        await apply_igdb_metadata(
            db, game, "Hades", _META,
            igdb_id=1234, replace_identity=False, clear_cover_on_null=False,
        )
    sync.assert_awaited_once()
    assert sync.await_args.kwargs["new_status"] == EnrichmentStatus.ENRICHED
