from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.endpoints.auth import get_current_user
from app.core.database import get_db
from app.models.user import User
from app.schemas.profile import ProfileResponse, ProfileSettingsUpdate

router = APIRouter()


def _to_response(user: User) -> ProfileResponse:
    return ProfileResponse(
        discord_id=user.discord_id,
        username=user.username,
        timezone=user.timezone,
        weekly_report_enabled=user.weekly_report_enabled,
        push_enabled=user.push_enabled,
    )


@router.get("/me", response_model=ProfileResponse)
async def get_me(user: User = Depends(get_current_user)):
    return _to_response(user)


@router.put("/settings", response_model=ProfileResponse)
async def update_settings(
    payload: ProfileSettingsUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    updates = payload.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(user, field, value)
    await db.commit()
    await db.refresh(user)
    return _to_response(user)
