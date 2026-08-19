import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.endpoints.auth import get_current_user, get_current_user_allow_pending
from app.core.database import get_db
from app.models.user import User
from app.schemas.deletion import DeletionStatusResponse
from app.schemas.profile import ProfileResponse, ProfileSettingsUpdate
from app.services.account_deletion import (
    applied_grace_days,
    cancel_deletion,
    days_left,
    schedule_deletion,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def _to_response(user: User) -> ProfileResponse:
    return ProfileResponse(
        discord_id=user.discord_id,
        username=user.username,
        timezone=user.timezone,
        language=user.language,
        weekly_report_enabled=user.weekly_report_enabled,
        push_enabled=user.push_enabled,
        is_admin=user.is_admin,
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


@router.post(
    "/me/deletion",
    response_model=DeletionStatusResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def delete_account(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user_allow_pending),
):
    # Depends on get_current_user_allow_pending (not get_current_user) so a
    # repeat call during the grace period doesn't 403 on its own guard.
    #
    # is_admin is seeded manually by design (no self-service toggle), so a
    # self-deleting admin would leave the admin dashboard unreachable without
    # direct psql access. Refuse rather than silently orphaning that state.
    if user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin accounts cannot self-delete",
        )

    user = await schedule_deletion(db, user)
    # schedule_deletion() always populates both timestamps.
    assert user.deletion_requested_at is not None
    assert user.purge_at is not None

    logger.info(
        "account_deletion_requested",
        extra={"discord_id": user.discord_id, "purge_at": user.purge_at.isoformat()},
    )

    return DeletionStatusResponse(
        deletion_requested_at=user.deletion_requested_at,
        purge_at=user.purge_at,
        days_left=days_left(user.purge_at),
        grace_days=applied_grace_days(user),
    )


@router.delete("/me/deletion", status_code=status.HTTP_200_OK)
async def cancel_account_deletion(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user_allow_pending),
):
    # get_current_user_allow_pending (not get_current_user): by definition the
    # caller of this endpoint is a scheduled account, so the deletion guard
    # would 403 them before they could ever cancel.
    cancelled = await cancel_deletion(db, user)
    if not cancelled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No deletion is scheduled for this account",
        )

    logger.info(
        "account_deletion_cancelled",
        extra={"discord_id": user.discord_id},
    )

    return {"detail": "Account deletion cancelled"}
