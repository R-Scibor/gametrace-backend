import hmac
import logging
import math
from datetime import datetime, timedelta, timezone

import httpx
import redis.exceptions
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
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

logger = logging.getLogger(__name__)

router = APIRouter()
bearer_scheme = HTTPBearer()
# auto_error=False so a missing Authorization header falls through to the bot
# credential check in get_current_or_bot_user, instead of HTTPBearer raising
# 403 on its own before that check ever runs.
_bearer_scheme_optional = HTTPBearer(auto_error=False)


def _token_expiry() -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=settings.session_token_expire_days)


@router.post("/login", response_model=LoginResponse, status_code=status.HTTP_200_OK)
async def login(
    payload: LoginRequest,
    db: AsyncSession = Depends(get_db),
    x_dev_login_secret: str | None = Header(default=None),
):
    # Name-only login proves no identity — it's a dev shortcut. It only exists when
    # DEV_LOGIN_SECRET is set, and callers must present it. Both misses look like a
    # missing route (404) so the endpoint gives nothing away when the API is exposed.
    secret = settings.dev_login_secret
    if not secret or not (
        x_dev_login_secret is not None
        and hmac.compare_digest(x_dev_login_secret, secret)
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")

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
        token=UserAuthToken.hash_token(token_value),
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
    except (redis.exceptions.RedisError, ConnectionError, OSError):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service temporarily unavailable",
        )

    if discord_id is None:
        try:
            await link_codes.record_failure(r, ip)
        except (redis.exceptions.RedisError, ConnectionError, OSError):
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
        token=UserAuthToken.hash_token(token_value),
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
    except discord_oauth.DiscordAuthError as exc:
        logger.warning("discord_login: authorization failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Discord authorization failed"
        )
    except discord_oauth.DiscordUpstreamError as exc:
        logger.warning("discord_login: upstream error: %s", exc)
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
        user_id=discord_id,
        token=UserAuthToken.hash_token(token_value),
        expires_at=_token_expiry(),
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
        select(UserAuthToken).where(
            UserAuthToken.token == UserAuthToken.hash_token(credentials.credentials)
        )
    )
    token = result.scalar_one_or_none()
    if token is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    await db.delete(token)
    await db.commit()


def _pending_deletion_error(user: User) -> HTTPException:
    """Build the structured 403 for an account scheduled for deletion.

    days_left is a CEILING of the remaining time so it never reads 0 while
    the account still exists (e.g. 25h left -> 2, not 1).
    """
    now = datetime.now(timezone.utc)
    delta = user.purge_at - now
    days_left = max(1, math.ceil(delta.total_seconds() / 86400))
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={
            "detail": "Account scheduled for deletion",
            "deletion_requested_at": user.deletion_requested_at.isoformat(),
            "purge_at": user.purge_at.isoformat(),
            "days_left": days_left,
        },
    )


async def get_current_user_allow_pending(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Dependency — resolves Bearer token to a User, refreshes last_active, raises 401 if
    invalid/expired. Unlike get_current_user, does NOT reject accounts scheduled for deletion.
    """
    result = await db.execute(
        select(UserAuthToken).where(
            UserAuthToken.token == UserAuthToken.hash_token(credentials.credentials)
        )
    )
    token = result.scalar_one_or_none()

    now = datetime.now(timezone.utc)
    if token is None or token.expires_at < now:
        if token is not None and token.expires_at < now:
            await db.delete(token)
            await db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")

    # Debounce the write: refreshing last_active + sliding expiry on every request
    # is one commit per authenticated call. The debounce window is tiny next to the
    # token lifetime, so active tokens still keep sliding forward.
    debounce = timedelta(minutes=settings.token_activity_debounce_minutes)
    if now - token.last_active >= debounce:
        token.last_active = now
        token.expires_at = _token_expiry()
        await db.commit()

    user = await db.get(User, token.user_id)
    return user


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Dependency — resolves Bearer token to a User, raises 403 if scheduled for deletion."""
    user = await get_current_user_allow_pending(credentials=credentials, db=db)
    if user.purge_at is not None:
        raise _pending_deletion_error(user)
    return user


async def get_bot_user(
    x_bot_service_secret: str | None = Header(default=None),
    x_discord_id: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Dependency — resolves a trusted bot-service secret + X-Discord-Id header to a User.

    This is a SEPARATE auth path from get_current_user (bearer tokens), for the
    Discord bot to read the API as a named user over the compose network. It is
    read-as-any-user by design: presenting the secret grants access to ANY
    discord_id, with no token or session tied to that user. Never expose this
    header pair outside the bot's own service-to-service calls, and never add
    it to the OpenAPI security schemes.
    """
    secret = settings.bot_service_secret
    if not secret or not (
        x_bot_service_secret is not None
        and hmac.compare_digest(x_bot_service_secret, secret)
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    if not x_discord_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    user = await db.get(User, x_discord_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    if user.purge_at is not None:
        raise _pending_deletion_error(user)

    return user


async def get_current_or_bot_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme_optional),
    x_bot_service_secret: str | None = Header(default=None),
    x_discord_id: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Dependency — resolves EITHER a bearer token OR bot-service headers to a User.

    Same route, two credential paths — not a parallel bot-only endpoint. The
    bearer-token path (web/mobile) behaves exactly as get_current_user always
    has; presenting an Authorization header always takes that path, so an
    invalid/expired token still 401s rather than silently falling through to
    the bot check. Only when no Authorization header is present at all does
    this fall through to get_bot_user. Deliberately built from plain Header()
    params (not a fastapi.security scheme) so it does not register as a public
    auth option in the OpenAPI schema — see get_bot_user's docstring.
    """
    if credentials is not None:
        return await get_current_user(credentials=credentials, db=db)
    return await get_bot_user(
        x_bot_service_secret=x_bot_service_secret, x_discord_id=x_discord_id, db=db
    )


async def require_admin(user: User = Depends(get_current_user)) -> User:
    """Dependency — passes through the current user if admin, raises 403 otherwise."""
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin privileges required")
    return user
