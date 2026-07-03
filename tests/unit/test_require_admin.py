import pytest
from fastapi import HTTPException

from app.api.v1.endpoints.auth import require_admin
from app.models.user import User


def _user(is_admin: bool) -> User:
    return User(discord_id="1", username="u", is_admin=is_admin)


async def test_require_admin_rejects_non_admin():
    with pytest.raises(HTTPException) as exc_info:
        await require_admin(user=_user(is_admin=False))
    assert exc_info.value.status_code == 403


async def test_require_admin_returns_admin_user():
    admin = _user(is_admin=True)
    result = await require_admin(user=admin)
    assert result is admin
