"""
Test factories — all use db.flush() so IDs are populated without committing.
The same session is shared between the factory and the endpoint under test,
so flushed-but-not-committed data is visible to all DB queries within a test.
"""
from datetime import datetime, timedelta, timezone

from app.models.game import EnrichmentStatus, Game, GameAlias, UserGamePreference
from app.models.session import GameSession, SessionSource, SessionStatus
from app.models.user import User, UserAuthToken, UserDevice


async def make_user(
    db,
    discord_id: str = "111111111111111111",
    username: str = "testuser",
    tz: str = "UTC",
) -> User:
    user = User(discord_id=discord_id, username=username, timezone=tz)
    db.add(user)
    await db.flush()
    return user


async def make_game(
    db,
    primary_name: str = "Test Game",
    enrichment_status: EnrichmentStatus = EnrichmentStatus.PENDING,
) -> Game:
    game = Game(primary_name=primary_name, enrichment_status=enrichment_status)
    db.add(game)
    await db.flush()
    return game


async def make_alias(db, game_id: int, process_name: str) -> GameAlias:
    alias = GameAlias(game_id=game_id, discord_process_name=process_name)
    db.add(alias)
    await db.flush()
    return alias


async def make_session(
    db,
    user_id: str,
    game_id: int,
    start_time: datetime,
    end_time: datetime | None = None,
    status: SessionStatus = SessionStatus.COMPLETED,
    source: SessionSource = SessionSource.MANUAL,
    notes: str | None = None,
    deleted_at: datetime | None = None,
) -> GameSession:
    duration = (
        int((end_time - start_time).total_seconds())
        if end_time is not None and status == SessionStatus.COMPLETED
        else None
    )
    session = GameSession(
        user_id=user_id,
        game_id=game_id,
        start_time=start_time,
        end_time=end_time,
        duration_seconds=duration,
        status=status,
        source=source,
        notes=notes,
        deleted_at=deleted_at,
    )
    db.add(session)
    await db.flush()
    return session


async def make_token(db, user_id: str) -> str:
    token_value = UserAuthToken.generate_token()
    token = UserAuthToken(
        user_id=user_id,
        token=token_value,
        expires_at=datetime.now(timezone.utc) + timedelta(days=30),
    )
    db.add(token)
    await db.flush()
    return token_value


async def make_pref(
    db,
    user_id: str,
    game_id: int,
    is_ignored: bool = False,
) -> UserGamePreference:
    pref = UserGamePreference(user_id=user_id, game_id=game_id, is_ignored=is_ignored)
    db.add(pref)
    await db.flush()
    return pref


async def make_device(
    db,
    user_id: str,
    fcm_token: str,
    device_type: str = "android",
) -> UserDevice:
    device = UserDevice(user_id=user_id, fcm_token=fcm_token, device_type=device_type)
    db.add(device)
    await db.flush()
    return device


def dt(hours_ago: float = 0, hours_from_now: float = 0) -> datetime:
    """Convenience: timezone-aware UTC datetime offset from now."""
    now = datetime.now(timezone.utc)
    return now - timedelta(hours=hours_ago) + timedelta(hours=hours_from_now)
