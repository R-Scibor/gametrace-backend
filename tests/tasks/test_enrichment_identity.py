"""Worker identity writes against the test database."""

import logging
from datetime import date

from app.models.game import CoverSource, EnrichmentStatus, Game
from app.services.game_matching import IGDBResult
from app.tasks.enrichment import _write_enrichment
from tests.factories import make_alias, make_game


def _hit(
    *,
    cover_url: str | None = "https://images.igdb.com/cover.jpg",
    confidence: float = 0.95,
    genres: list[str] | None = None,
    themes: list[str] | None = None,
    developers: list[str] | None = None,
    publishers: list[str] | None = None,
    first_release_date: date | None = date(2020, 12, 10),
    name: str | None = "Cyberpunk 2077",
    igdb_id: int | None = 1877,
) -> IGDBResult:
    return IGDBResult(
        cover_url=cover_url,
        confidence=confidence,
        genres=["RPG"] if genres is None else genres,
        themes=["Action"] if themes is None else themes,
        developers=["CD Projekt"] if developers is None else developers,
        publishers=["CD Projekt"] if publishers is None else publishers,
        first_release_date=first_release_date,
        name=name,
        igdb_id=igdb_id,
    )


async def test_igdb_first_fill_sets_id_and_title_and_keeps_the_alias(db):
    game = await make_game(db, "cyberpunk2077.exe", enrichment_status=EnrichmentStatus.PENDING)
    game.cover_image_url = "https://already.example/cover.jpg"
    await db.flush()
    alias = await make_alias(db, game.id, "cyberpunk2077.exe")

    status, _, ext_id = await _write_enrichment(
        db, game.id,
        igdb_result=_hit(cover_url=None),
        steam=(None, None, None),
    )

    await db.refresh(game)
    await db.refresh(alias)
    assert status == EnrichmentStatus.ENRICHED
    assert ext_id == "igdb:1877"
    assert game.external_api_id == "igdb:1877"
    assert game.primary_name == "Cyberpunk 2077"
    assert game.cover_image_url == "https://already.example/cover.jpg"
    assert game.genres == ["RPG"]
    assert alias.discord_process_name == "cyberpunk2077.exe"
    assert alias.game_id == game.id


async def test_steam_first_fill_sets_prefixed_id_and_does_not_fill_genres(db):
    game = await make_game(db, "hk.exe", enrichment_status=EnrichmentStatus.PENDING)
    status, cover, ext_id = await _write_enrichment(
        db, game.id,
        igdb_result=_hit(confidence=0.2, igdb_id=None, name=None, genres=[]),
        steam=(1145360, "https://steam.example/cover.jpg", "Hollow Knight"),
    )
    await db.refresh(game)
    assert status == EnrichmentStatus.ENRICHED
    assert cover == "https://steam.example/cover.jpg"
    assert ext_id == "steam:1145360"
    assert game.primary_name == "Hollow Knight"
    assert game.genres == []
    assert game.cover_source == CoverSource.EXTERNAL


async def test_requeue_of_custom_steam_row_refreshes_genres_only(db):
    game = await make_game(
        db, "Keep Me", enrichment_status=EnrichmentStatus.ENRICHED, genres=["old"],
    )
    game.external_api_id = "steam:1"
    game.cover_source = CoverSource.CUSTOM
    game.cover_image_url = "/covers/keep.jpg"
    await db.flush()

    await _write_enrichment(
        db, game.id,
        igdb_result=_hit(),
        steam=(None, None, None),
    )
    await db.refresh(game)
    assert game.external_api_id == "steam:1"
    assert game.primary_name == "Keep Me"
    assert game.cover_image_url == "/covers/keep.jpg"
    assert game.cover_source == CoverSource.CUSTOM
    assert game.genres == ["RPG"]


async def test_requeue_of_a_bare_id_does_not_prefix_or_rename(db):
    game = await make_game(db, "Team Fortress 2", enrichment_status=EnrichmentStatus.ENRICHED)
    game.external_api_id = "440"
    await db.flush()

    await _write_enrichment(
        db, game.id,
        igdb_result=_hit(name="Team Fortress 2", igdb_id=1, genres=["Shooter"]),
        steam=(None, None, None),
    )
    await db.refresh(game)
    assert game.external_api_id == "440"
    assert game.primary_name == "Team Fortress 2"
    assert game.genres == ["Shooter"]


async def test_steam_requeue_refreshes_a_non_custom_cover_and_leaves_metadata(db):
    game = await make_game(
        db, "Keep Me",
        enrichment_status=EnrichmentStatus.ENRICHED,
        genres=["old"],
        first_release_date=date(2017, 2, 24),
    )
    game.external_api_id = "steam:1"
    game.cover_image_url = "https://old.example/cover.jpg"
    await db.flush()

    await _write_enrichment(
        db, game.id,
        igdb_result=_hit(confidence=0.1, igdb_id=None, name=None),
        steam=(999, "https://new.example/cover.jpg", "Other Name"),
    )
    await db.refresh(game)
    assert game.external_api_id == "steam:1"
    assert game.primary_name == "Keep Me"
    assert game.cover_image_url == "https://new.example/cover.jpg"
    assert game.genres == ["old"]
    assert game.first_release_date == date(2017, 2, 24)


async def test_custom_null_id_writes_identity_and_leaves_the_cover(db):
    game = await make_game(db, "game.exe", enrichment_status=EnrichmentStatus.PENDING)
    game.cover_source = CoverSource.CUSTOM
    game.cover_image_url = "/covers/mine.jpg"
    await db.flush()

    await _write_enrichment(
        db, game.id, igdb_result=_hit(), steam=(None, None, None),
    )
    await db.refresh(game)
    assert game.external_api_id == "igdb:1877"
    assert game.primary_name == "Cyberpunk 2077"
    assert game.genres == ["RPG"]
    assert game.cover_image_url == "/covers/mine.jpg"
    assert game.cover_source == CoverSource.CUSTOM


async def test_unique_violation_leaves_the_row_and_does_not_demote(db, caplog):
    holder = await make_game(db, "Holder", enrichment_status=EnrichmentStatus.ENRICHED)
    holder.external_api_id = "igdb:1877"
    await db.flush()
    stub = await make_game(db, "game.exe", enrichment_status=EnrichmentStatus.PENDING, genres=["old"])
    await db.flush()

    with caplog.at_level(logging.INFO, logger="app.tasks.enrichment"):
        status, cover, ext_id = await _write_enrichment(
            db, stub.id, igdb_result=_hit(), steam=(None, None, None),
        )

    await db.refresh(stub)
    assert status == EnrichmentStatus.PENDING
    assert cover is None
    assert ext_id is None
    assert stub.enrichment_status == EnrichmentStatus.PENDING
    assert stub.primary_name == "game.exe"
    assert stub.external_api_id is None
    assert stub.genres == ["old"]
    assert "enrich_game.external_id_taken" in caplog.text
    holder_still = await db.get(Game, holder.id)
    assert holder_still is not None
    assert holder_still.external_api_id == "igdb:1877"


async def test_steam_requeue_leaves_a_custom_cover(db):
    game = await make_game(
        db, "Keep Me", enrichment_status=EnrichmentStatus.ENRICHED, genres=["old"],
    )
    game.external_api_id = "steam:1"
    game.cover_source = CoverSource.CUSTOM
    game.cover_image_url = "/covers/keep.jpg"
    await db.flush()

    await _write_enrichment(
        db, game.id,
        igdb_result=_hit(confidence=0.1, igdb_id=None, name=None),
        steam=(999, "https://new.example/cover.jpg", "Other Name"),
    )
    await db.refresh(game)
    assert game.external_api_id == "steam:1"
    assert game.primary_name == "Keep Me"
    assert game.cover_image_url == "/covers/keep.jpg"
    assert game.cover_source == CoverSource.CUSTOM
    assert game.genres == ["old"]
