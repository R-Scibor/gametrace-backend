"""DB invariant: at most one live ONGOING session per user."""
import pytest
from sqlalchemy.exc import IntegrityError

from app.models.session import GameSession, SessionSource, SessionStatus
from tests.factories import dt, make_game, make_user

pytestmark = pytest.mark.asyncio


async def test_second_ongoing_session_violates_unique_index(db):
    user = await make_user(db)
    game_a = await make_game(db, "Game A")
    game_b = await make_game(db, "Game B")

    db.add(
        GameSession(
            user_id=user.discord_id,
            game_id=game_a.id,
            start_time=dt(hours_ago=1),
            status=SessionStatus.ONGOING,
            source=SessionSource.BOT,
        )
    )
    await db.commit()

    db.add(
        GameSession(
            user_id=user.discord_id,
            game_id=game_b.id,
            start_time=dt(),
            status=SessionStatus.ONGOING,
            source=SessionSource.BOT,
        )
    )
    with pytest.raises(IntegrityError):
        await db.commit()

    await db.rollback()