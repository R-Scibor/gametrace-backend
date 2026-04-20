from datetime import datetime, timezone

from fastapi import APIRouter, Depends, status
from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.endpoints.auth import get_current_user
from app.core.database import get_db
from app.models.user import User, UserDevice
from app.schemas.notifications import (
    DeviceRegisterRequest,
    DeviceResponse,
    DeviceUnregisterRequest,
)

router = APIRouter()


@router.post("/register-token", response_model=DeviceResponse)
async def register_token(
    payload: DeviceRegisterRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Upsert an FCM token for the current user.

    A single ON CONFLICT (fcm_token) UPDATE handles all three cases:
    brand-new token, same user re-registering, and token migration
    between users on the same physical device.
    """
    now = datetime.now(timezone.utc)
    stmt = (
        pg_insert(UserDevice)
        .values(
            user_id=user.discord_id,
            fcm_token=payload.fcm_token,
            device_type=payload.device_type,
            last_active=now,
        )
        .on_conflict_do_update(
            index_elements=["fcm_token"],
            set_={
                "user_id": user.discord_id,
                "device_type": payload.device_type,
                "last_active": now,
            },
        )
    )
    await db.execute(stmt)
    await db.commit()
    return DeviceResponse(fcm_token=payload.fcm_token, device_type=payload.device_type)


@router.delete("/register-token", status_code=status.HTTP_204_NO_CONTENT)
async def unregister_token(
    payload: DeviceUnregisterRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Delete an FCM token (only for the current user, silently ok if missing)."""
    await db.execute(
        delete(UserDevice).where(
            UserDevice.user_id == user.discord_id,
            UserDevice.fcm_token == payload.fcm_token,
        )
    )
    await db.commit()
