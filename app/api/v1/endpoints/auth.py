from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.models.user import User, UserAuthToken
from app.schemas.auth import LoginRequest, LoginResponse

router = APIRouter()
bearer_scheme = HTTPBearer()


def _token_expiry() -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=settings.session_token_expire_days)


@router.post("/login", response_model=LoginResponse, status_code=status.HTTP_200_OK)
async def login(payload: LoginRequest, db: AsyncSession = Depends(get_db)):
    # User must be pre-registered via Discord /login slash command
    result = await db.execute(select(User).where(User.username == payload.username))
    user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found. Run /login on Discord first.",
        )

    if payload.timezone != "UTC":
        user.timezone = payload.timezone

    # Issue a new token
    token_value = UserAuthToken.generate_token()
    token = UserAuthToken(
        user_id=user.discord_id,
        token=token_value,
        expires_at=_token_expiry(),
    )
    db.add(token)
    await db.commit()
    await db.refresh(user)

    return LoginResponse(
        token=token_value,
        discord_id=user.discord_id,
        username=user.username,
        timezone=user.timezone,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(UserAuthToken).where(UserAuthToken.token == credentials.credentials)
    )
    token = result.scalar_one_or_none()
    if token is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    await db.delete(token)
    await db.commit()


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Dependency — resolves Bearer token to a User, refreshes last_active, raises 401 if invalid/expired."""
    result = await db.execute(
        select(UserAuthToken).where(UserAuthToken.token == credentials.credentials)
    )
    token = result.scalar_one_or_none()

    now = datetime.now(timezone.utc)
    if token is None or token.expires_at < now:
        if token is not None and token.expires_at < now:
            await db.delete(token)
            await db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")

    token.last_active = now
    token.expires_at = _token_expiry()
    await db.commit()

    user = await db.get(User, token.user_id)
    return user
