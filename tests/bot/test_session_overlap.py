"""Range exclusion: live, non-flicker ONGOING and COMPLETED sessions cannot overlap."""

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.bot.session_manager import start_session
from app.models.game import Game
from app.models.session import GameSession, SessionSource, SessionStatus
from tests.factories import dt, make_game, make_session, make_user


async def test_overlapping_completed_rows_violate_the_exclusion_constraint(db):
    user = await make_user(db)
    game = await make_game(db)
    await make_session(db, user.discord_id, game.id, dt(hours_ago=3), dt(hours_ago=1))
    await db.commit()

    db.add(
        GameSession(
            user_id=user.discord_id,
            game_id=game.id,
            start_time=dt(hours_ago=2),
            end_time=dt(hours_ago=0.5),
            duration_seconds=5400,
            status=SessionStatus.COMPLETED,
            source=SessionSource.MANUAL,
        )
    )
    with pytest.raises(IntegrityError):
        await db.commit()
    await db.rollback()


async def test_error_flicker_and_trashed_rows_may_overlap(db):
    user = await make_user(db)
    game = await make_game(db)
    await make_session(db, user.discord_id, game.id, dt(hours_ago=3), dt(hours_ago=1))
    await db.commit()

    for kwargs in (
        {"status": SessionStatus.ERROR},
        {"is_flicker": True},
        {"deleted_at": dt(hours_ago=0.1)},
    ):
        await make_session(
            db,
            user.discord_id,
            game.id,
            dt(hours_ago=2),
            dt(hours_ago=0.5),
            **kwargs,
        )
        await db.commit()


async def test_adjacent_sessions_and_other_users_may_share_an_endpoint(db):
    user = await make_user(db)
    other = await make_user(db, discord_id="900000000000000031", username="otheroverlap")
    game = await make_game(db)
    boundary = dt(hours_ago=1)
    await make_session(db, user.discord_id, game.id, dt(hours_ago=2), boundary)
    await db.commit()

    await make_session(db, user.discord_id, game.id, boundary, dt(hours_ago=0.25))
    await make_session(db, other.discord_id, game.id, dt(hours_ago=2), dt(hours_ago=0.25))
    await db.commit()

    rows = await db.execute(select(GameSession).where(GameSession.game_id == game.id))
    assert len(rows.scalars().all()) == 3


async def test_start_session_returns_none_when_this_instant_is_already_covered(db):
    user = await make_user(db)
    game = await make_game(db)
    covering = await make_session(
        db,
        user.discord_id,
        game.id,
        dt(hours_ago=1),
        dt(hours_from_now=1),
        source=SessionSource.MANUAL,
    )
    await db.commit()
    user_id = user.discord_id
    covering_id = covering.id

    result = await start_session(db, user_id, game.id)

    assert result is None
    rows = await db.execute(select(GameSession).where(GameSession.user_id == user_id))
    assert [row.id for row in rows.scalars().all()] == [covering_id]


async def test_start_or_resume_does_not_reopen_into_a_covered_instant(db):
    from app.bot.session_manager import start_or_resume_session

    user = await make_user(db)
    game = await make_game(db, "Hades")
    other = await make_game(db, "Minecraft")
    # Closed 90s ago → 30s ago. The manual row starts after that end, so it
    # does not overlap the closed row, and it does cover "now". Reopening the
    # bot row would run to infinity and hit it.
    candidate = await make_session(
        db,
        user.discord_id,
        game.id,
        start_time=dt(hours_ago=90 / 3600),
        end_time=dt(hours_ago=30 / 3600),
        status=SessionStatus.COMPLETED,
        source=SessionSource.BOT,
    )
    await make_session(
        db,
        user.discord_id,
        other.id,
        dt(hours_ago=10 / 3600),
        dt(hours_from_now=1),
        source=SessionSource.MANUAL,
    )
    await db.commit()

    result = await start_or_resume_session(db, user.discord_id, game.id)

    assert result is None
    await db.refresh(candidate)
    assert candidate.status == SessionStatus.COMPLETED
    assert candidate.end_time is not None


async def test_start_session_overlap_none_keeps_an_outer_stub(db):
    user = await make_user(db)
    game = await make_game(db)
    await make_session(
        db,
        user.discord_id,
        game.id,
        dt(hours_ago=1),
        dt(hours_from_now=1),
        source=SessionSource.MANUAL,
    )
    await db.commit()
    stub = Game(primary_name="kept-stub")
    db.add(stub)
    await db.flush()
    stub_id = stub.id

    result = await start_session(db, user.discord_id, game.id)

    assert result is None
    assert await db.get(Game, stub_id) is not None
