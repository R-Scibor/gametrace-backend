from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.redis import get_redis
from app.models.user import User, UserAuthToken
from app.schemas.auth import (
    DiscordCallbackRequest,
    LinkCodeRequest,
    LoginRequest,
    LoginResponse,
)
from app.services import discord_oauth, link_codes

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
        is_admin=user.is_admin,
    )


@router.post("/link", response_model=LoginResponse, status_code=status.HTTP_200_OK)
async def link_login(
    payload: LinkCodeRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    if not settings.link_code_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Login link codes are not configured",
        )

    try:
        r = get_redis()
        ip = link_codes.get_client_ip(request)
        retry_after = await link_codes.check_lockout(r, ip)
        if retry_after is not None:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many failed attempts",
                headers={"Retry-After": str(retry_after)},
            )
        discord_id = await link_codes.redeem_code(r, payload.code)
    except link_codes.LinkCodesNotConfigured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Login link codes are not configured",
        )
    except (ConnectionError, OSError):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service temporarily unavailable",
        )

    if discord_id is None:
        try:
            await link_codes.record_failure(r, ip)
        except (ConnectionError, OSError):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Service temporarily unavailable",
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired code",
        )

    user = await db.get(User, discord_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired code",
        )

    if payload.timezone != "UTC":
        user.timezone = payload.timezone

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
        is_admin=user.is_admin,
    )


@router.post("/discord", response_model=LoginResponse, status_code=status.HTTP_200_OK)
async def discord_login(payload: DiscordCallbackRequest, db: AsyncSession = Depends(get_db)):
    if payload.redirect_uri not in settings.discord_redirect_uri_allowlist:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="redirect_uri not allowed"
        )

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            access_token = await discord_oauth.exchange_code(
                client, payload.code, payload.code_verifier, payload.redirect_uri
            )
            identity = await discord_oauth.fetch_identity(client, access_token)
            guilds = await discord_oauth.fetch_guilds(client, access_token)
    except discord_oauth.DiscordAuthError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Discord authorization failed"
        )
    except discord_oauth.DiscordUpstreamError:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="Discord unavailable"
        )

    discord_id = identity["id"]
    username = identity["username"]

    user = await db.get(User, discord_id)
    if user is None:
        user = User(discord_id=discord_id, username=username)
        db.add(user)
    else:
        user.username = username  # sync in case the Discord username changed

    guild_ids = settings.discord_guild_id_set
    needs_server_join = bool(guild_ids) and not (guild_ids & guilds)

    token_value = UserAuthToken.generate_token()
    token = UserAuthToken(
        user_id=discord_id, token=token_value, expires_at=_token_expiry()
    )
    db.add(token)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Discord username conflicts with an existing account",
        )
    await db.refresh(user)

    return LoginResponse(
        token=token_value,
        discord_id=user.discord_id,
        username=user.username,
        timezone=user.timezone,
        is_admin=user.is_admin,
        needs_server_join=needs_server_join,
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


async def require_admin(user: User = Depends(get_current_user)) -> User:
    """Dependency — passes through the current user if admin, raises 403 otherwise."""
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin privileges required")
    return user
