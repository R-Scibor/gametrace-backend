from enum import Enum

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.game import GameAlias


class AliasResult(Enum):
    CREATED = "created"
    EXISTS_SAME_GAME = "exists_same_game"
    CONFLICT = "conflict"


async def add_alias(
    db: AsyncSession, game_id: int, name: str
) -> tuple[AliasResult, int | None]:
    """Race-safe alias insert. Caller owns the transaction; does not commit."""
    stmt = (
        pg_insert(GameAlias)
        .values(game_id=game_id, discord_process_name=name)
        .on_conflict_do_nothing(index_elements=["discord_process_name"])
        .returning(GameAlias.game_id)
    )
    inserted = (await db.execute(stmt)).scalar_one_or_none()
    if inserted is not None:
        return AliasResult.CREATED, None

    owner_id = (
        await db.execute(
            select(GameAlias.game_id).where(GameAlias.discord_process_name == name)
        )
    ).scalar_one()

    if owner_id == game_id:
        return AliasResult.EXISTS_SAME_GAME, game_id
    return AliasResult.CONFLICT, owner_id