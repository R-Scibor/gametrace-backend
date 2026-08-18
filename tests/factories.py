"""
Test factories — all use db.flush() so IDs are populated without committing.
The same session is shared between the factory and the endpoint under test,
so flushed-but-not-committed data is visible to all DB queries within a test.
"""
from datetime import UTC, datetime, timedelta

from app.models.demo_seed import DemoSeedPreference, DemoSeedSession
from app.models.game import EnrichmentStatus, Game, GameAlias, UserGamePreference
from app.models.report import Report
from app.models.session import GameSession, SessionSource, SessionStatus
from app.models.user import User, UserAuthToken, UserDevice
from app.models.voice_usage import VoiceUsage


async def make_user(
    db,
    discord_id: str = "111111111111111111",
    username: str = "testuser",
    tz: str = "UTC",
    is_admin: bool = False,
    deletion_requested_at: datetime | None = None,
    purge_at: datetime | None = None,
) -> User:
    user = User(
        discord_id=discord_id,
        username=username,
        timezone=tz,
        is_admin=is_admin,
        deletion_requested_at=deletion_requested_at,
        purge_at=purge_at,
    )
    db.add(user)
    await db.flush()
    return user


async def make_game(
    db,
    primary_name: str = "Test Game",
    enrichment_status: EnrichmentStatus = EnrichmentStatus.PENDING,
    genres: list[str] | None = None,
    themes: list[str] | None = None,
    developers: list[str] | None = None,
    publishers: list[str] | None = None,
    first_release_date=None,
) -> Game:
    game = Game(
        primary_name=primary_name,
        enrichment_status=enrichment_status,
        genres=genres or [],
        themes=themes or [],
        developers=developers or [],
        publishers=publishers or [],
        first_release_date=first_release_date,
    )
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
    is_flicker: bool = False,
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
        is_flicker=is_flicker,
    )
    db.add(session)
    await db.flush()
    return session


async def make_token(db, user_id: str) -> str:
    """Issue a token like production does: store the hash, return the raw value."""
    token_value = UserAuthToken.generate_token()
    token = UserAuthToken(
        user_id=user_id,
        token=UserAuthToken.hash_token(token_value),
        expires_at=datetime.now(UTC) + timedelta(days=30),
    )
    db.add(token)
    await db.flush()
    return token_value


async def make_pref(
    db,
    user_id: str,
    game_id: int,
    is_ignored: bool = False,
    is_accepted: bool | None = None,
) -> UserGamePreference:
    pref = UserGamePreference(
        user_id=user_id,
        game_id=game_id,
        is_ignored=is_ignored,
        is_accepted=is_accepted,
    )
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


async def make_report(
    db,
    user_id: str,
    message: str = "bug",
    context: dict | None = None,
    status: str = "open",
    created_at: datetime | None = None,
    admin_note: str | None = None,
) -> Report:
    report = Report(
        user_id=user_id,
        message=message,
        context=context
        or {"screen": "Home", "platform": "android", "osVersion": "14", "appVersion": "1.0.0"},
        status=status,
        admin_note=admin_note,
    )
    if created_at is not None:
        report.created_at = created_at
    db.add(report)
    await db.flush()
    return report


async def make_demo_seed_session(
    db,
    game_id: int,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    duration_seconds: int | None = None,
    status: str = "COMPLETED",
    source: str = "MANUAL",
) -> DemoSeedSession:
    seed_session = DemoSeedSession(
        game_id=game_id,
        start_time=start_time or dt(hours_ago=3),
        end_time=end_time,
        duration_seconds=duration_seconds,
        status=status,
        source=source,
    )
    db.add(seed_session)
    await db.flush()
    return seed_session


async def make_demo_seed_preference(
    db,
    game_id: int,
    is_ignored: bool = False,
    is_accepted: bool | None = None,
    custom_tag: str | None = None,
) -> DemoSeedPreference:
    seed_pref = DemoSeedPreference(
        game_id=game_id,
        is_ignored=is_ignored,
        is_accepted=is_accepted,
        custom_tag=custom_tag,
    )
    db.add(seed_pref)
    await db.flush()
    return seed_pref


async def make_voice_usage(
    db,
    user_id: str,
    created_at: datetime | None = None,
    audio_seconds: float | None = 30.0,
    detected_language: str | None = "pl",
    game_resolved: bool = True,
    fields_extracted: int = 3,
) -> VoiceUsage:
    """One row per successful /voice/transcribe call — the source the daily
    voice quota counts from."""
    usage = VoiceUsage(
        user_id=user_id,
        audio_seconds=audio_seconds,
        detected_language=detected_language,
        game_resolved=game_resolved,
        fields_extracted=fields_extracted,
    )
    if created_at is not None:
        usage.created_at = created_at
    db.add(usage)
    await db.flush()
    return usage


def dt(hours_ago: float = 0, hours_from_now: float = 0) -> datetime:
    """Convenience: timezone-aware UTC datetime offset from now."""
    now = datetime.now(UTC)
    return now - timedelta(hours=hours_ago) + timedelta(hours=hours_from_now)
