import logging

from sqlalchemy import select

from app.models.game import EnrichmentStatus, Game, GameAlias
from app.services.game_aliases import add_alias, note_application_id
from tests.factories import make_alias, make_game

STORED = "10000000000000000"
OTHER = "10000000000000001"


async def _alias(db, name: str) -> GameAlias:
    row = (
        await db.execute(select(GameAlias).where(GameAlias.discord_process_name == name))
    ).scalar_one()
    return row


async def test_null_id_and_missing_alias_do_nothing(db):
    game = await make_game(db, "Hades")
    alias = await make_alias(db, game.id, "Hades.exe")

    await note_application_id(db, "Hades.exe", None)
    await note_application_id(db, "missing.exe", STORED)
    await db.commit()

    await db.refresh(alias)
    assert alias.application_id is None


async def test_first_id_is_stored_and_the_same_id_does_not_log(db, caplog):
    game = await make_game(db, "Hades")
    alias = await make_alias(db, game.id, "Hades.exe")

    with caplog.at_level(logging.INFO):
        await note_application_id(db, "Hades.exe", STORED)
        await note_application_id(db, "Hades.exe", STORED)
        await db.commit()

    await db.refresh(alias)
    assert alias.application_id == STORED
    assert "presence.application_id_conflict" not in caplog.text


async def test_different_id_does_not_overwrite_or_change_status(db, caplog):
    game = await make_game(db, "Hades", enrichment_status=EnrichmentStatus.ENRICHED)
    alias = await make_alias(db, game.id, "Hades.exe")
    alias.application_id = STORED
    await db.commit()

    with caplog.at_level(logging.INFO):
        await note_application_id(db, "Hades.exe", OTHER)
        await db.commit()

    await db.refresh(alias)
    await db.refresh(game)
    assert alias.application_id == STORED
    assert game.enrichment_status == EnrichmentStatus.ENRICHED
    record = next(
        row for row in caplog.records if row.message == "presence.application_id_conflict"
    )
    assert record.game_id == game.id
    assert record.alias_id == alias.id
    assert record.stored_application_id == STORED
    assert record.application_id == OTHER


async def test_add_alias_still_ignores_an_application_id(db):
    game = await make_game(db, "Hades")
    await add_alias(db, game.id, "Hades.exe")
    await db.commit()

    alias = await _alias(db, "Hades.exe")
    assert alias.application_id is None
    names = (await db.execute(select(Game.primary_name))).scalars().all()
    assert names == ["Hades"]
