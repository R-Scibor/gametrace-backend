from sqlalchemy import select

from app.models.game import EnrichmentStatus, UserGamePreference
from app.services.game_review import (
    clear_review_on_enriched,
    ensure_inbox_for_user,
    mark_needs_review_inbox,
    sync_review_preferences,
)
from tests.factories import dt, make_game, make_pref, make_session


async def test_mark_needs_review_inbox_creates_pref(db, user):
    game = await make_game(db, "Unknown Stub", EnrichmentStatus.NEEDS_REVIEW)
    await make_session(db, user.discord_id, game.id, dt(hours_ago=3), dt(hours_ago=2))

    await mark_needs_review_inbox(db, game.id)
    await db.commit()

    pref = (
        await db.execute(
            select(UserGamePreference).where(
                UserGamePreference.user_id == user.discord_id,
                UserGamePreference.game_id == game.id,
            )
        )
    ).scalar_one()
    assert pref.is_accepted is False


async def test_mark_needs_review_inbox_does_not_overwrite_accepted(db, user):
    game = await make_game(db, "Kept Stub", EnrichmentStatus.NEEDS_REVIEW)
    await make_session(db, user.discord_id, game.id, dt(hours_ago=3), dt(hours_ago=2))
    await make_pref(db, user.discord_id, game.id, is_accepted=True)

    await mark_needs_review_inbox(db, game.id)
    await db.commit()

    pref = (
        await db.execute(
            select(UserGamePreference).where(
                UserGamePreference.user_id == user.discord_id,
                UserGamePreference.game_id == game.id,
            )
        )
    ).scalar_one()
    assert pref.is_accepted is True


async def test_clear_review_on_enriched_nulls_is_accepted(db, user):
    game = await make_game(db, "Resolved", EnrichmentStatus.ENRICHED)
    await make_pref(db, user.discord_id, game.id, is_accepted=False)

    await clear_review_on_enriched(db, game.id)
    await db.commit()

    pref = (
        await db.execute(
            select(UserGamePreference).where(
                UserGamePreference.user_id == user.discord_id,
                UserGamePreference.game_id == game.id,
            )
        )
    ).scalar_one()
    assert pref.is_accepted is None
    assert pref.is_ignored is False


async def test_sync_review_preferences_enriched_from_review(db, user):
    game = await make_game(db, "Was Unknown", EnrichmentStatus.NEEDS_REVIEW)
    await make_session(db, user.discord_id, game.id, dt(hours_ago=3), dt(hours_ago=2))
    await make_pref(db, user.discord_id, game.id, is_accepted=False, is_ignored=True)

    await sync_review_preferences(
        db,
        game.id,
        previous_status=EnrichmentStatus.NEEDS_REVIEW,
        new_status=EnrichmentStatus.ENRICHED,
    )
    await db.commit()

    pref = (
        await db.execute(
            select(UserGamePreference).where(
                UserGamePreference.user_id == user.discord_id,
                UserGamePreference.game_id == game.id,
            )
        )
    ).scalar_one()
    assert pref.is_accepted is None
    assert pref.is_ignored is True


async def test_ensure_inbox_for_user_idempotent(db, user):
    game = await make_game(db, "Launcher.exe", EnrichmentStatus.NEEDS_REVIEW)

    await ensure_inbox_for_user(db, game.id, user.discord_id)
    await ensure_inbox_for_user(db, game.id, user.discord_id)
    await db.commit()

    rows = (
        await db.execute(
            select(UserGamePreference).where(
                UserGamePreference.user_id == user.discord_id,
                UserGamePreference.game_id == game.id,
            )
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].is_accepted is False