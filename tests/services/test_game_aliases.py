from sqlalchemy import func, select

from app.models.game import GameAlias
from app.services.game_aliases import AliasResult, add_alias
from tests.factories import make_alias, make_game


async def test_add_alias_creates_fresh_name(db):
    game = await make_game(db, "Fresh Game")
    name = "fresh.exe"

    result, owner_id = await add_alias(db, game.id, name)

    assert result is AliasResult.CREATED
    assert owner_id is None

    count = (
        await db.execute(
            select(func.count()).where(GameAlias.discord_process_name == name)
        )
    ).scalar_one()
    assert count == 1


async def test_add_alias_exists_same_game(db):
    game = await make_game(db, "Same Game")
    name = "same.exe"
    await make_alias(db, game.id, name)

    result, owner_id = await add_alias(db, game.id, name)

    assert result is AliasResult.EXISTS_SAME_GAME
    assert owner_id == game.id

    count = (
        await db.execute(
            select(func.count()).where(GameAlias.discord_process_name == name)
        )
    ).scalar_one()
    assert count == 1


async def test_add_alias_conflict_other_game(db):
    game_a = await make_game(db, "Game A")
    game_b = await make_game(db, "Game B")
    name = "taken.exe"
    await make_alias(db, game_a.id, name)

    result, owner_id = await add_alias(db, game_b.id, name)

    assert result is AliasResult.CONFLICT
    assert owner_id == game_a.id

    count = (
        await db.execute(
            select(func.count()).where(GameAlias.discord_process_name == name)
        )
    ).scalar_one()
    assert count == 1