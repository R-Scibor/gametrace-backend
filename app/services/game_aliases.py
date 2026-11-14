import logging
from enum import Enum

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.game import GameAlias

logger = logging.getLogger(__name__)


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


async def note_application_id(
    db: AsyncSession, process_name: str, application_id: str | None
) -> None:
    """Store the first usable id on the surviving alias. Caller commits.

    A null id, or no alias, does nothing. An equal id does nothing.
    A different id is logged and does not overwrite. This never enqueues,
    never starts or closes a session, and never changes enrichment_status.

    The conditional update is what makes the first commit win. An id written
    inside ``add_alias`` would disappear when that insert loses the race.
    """
    if application_id is None:
        return

    updated = (
        await db.execute(
            update(GameAlias)
            .where(
                GameAlias.discord_process_name == process_name,
                GameAlias.application_id.is_(None),
            )
            .values(application_id=application_id)
            .returning(GameAlias.id)
        )
    ).scalar_one_or_none()
    if updated is not None:
        alias = await db.get(GameAlias, updated)
        if alias is not None:
            alias.application_id = application_id
        return

    alias = (
        await db.execute(
            select(GameAlias).where(GameAlias.discord_process_name == process_name)
        )
    ).scalar_one_or_none()
    if alias is None or alias.application_id == application_id:
        return
    logger.info(
        "presence.application_id_conflict",
        extra={
            "game_id": alias.game_id,
            "alias_id": alias.id,
            "stored_application_id": alias.application_id,
            "application_id": application_id,
        },
    )


def choose_application_id(rows: list[tuple[int, str | None]]) -> str | None:
    """Application id on the lowest alias id that has one. Other ids are ignored."""
    chosen: tuple[int, str] | None = None
    for alias_id, application_id in rows:
        if not application_id:
            continue
        if chosen is None or alias_id < chosen[0]:
            chosen = (alias_id, application_id)
    if chosen is None:
        return None
    return chosen[1]

