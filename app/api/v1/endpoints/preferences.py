from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.endpoints.auth import get_current_user
from app.core.database import get_db
from app.models.game import EnrichmentStatus, Game, UserGamePreference
from app.models.user import User
from app.schemas.preferences import PreferenceResponse, PreferenceUpdate

router = APIRouter()


@router.put("/{game_id}", response_model=PreferenceResponse)
async def upsert_preference(
    game_id: int,
    payload: PreferenceUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    game = await db.get(Game, game_id)
    if game is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Game not found")

    if (
        "is_accepted" in payload.model_fields_set
        and payload.is_accepted is not None
        and game.enrichment_status != EnrichmentStatus.NEEDS_REVIEW
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="is_accepted only applies to NEEDS_REVIEW games",
        )

    insert_values: dict = {
        "user_id": user.discord_id,
        "game_id": game_id,
        "is_ignored": payload.is_ignored,
        "custom_tag": payload.custom_tag,
    }
    update_values: dict = {
        "is_ignored": payload.is_ignored,
        "custom_tag": payload.custom_tag,
    }
    if "is_accepted" in payload.model_fields_set:
        insert_values["is_accepted"] = payload.is_accepted
        update_values["is_accepted"] = payload.is_accepted

    stmt = (
        pg_insert(UserGamePreference)
        .values(**insert_values)
        .on_conflict_do_update(
            index_elements=["user_id", "game_id"],
            set_=update_values,
        )
    )
    await db.execute(stmt)
    await db.commit()

    pref = (
        await db.execute(
            select(UserGamePreference).where(
                UserGamePreference.user_id == user.discord_id,
                UserGamePreference.game_id == game_id,
            )
        )
    ).scalar_one()

    return PreferenceResponse(
        game_id=game_id,
        is_ignored=pref.is_ignored,
        is_accepted=pref.is_accepted,
        custom_tag=pref.custom_tag,
    )


@router.delete("/{game_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_preference(
    game_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await db.execute(
        delete(UserGamePreference).where(
            UserGamePreference.user_id == user.discord_id,
            UserGamePreference.game_id == game_id,
        )
    )
    await db.commit()