from app.schemas.session import SessionResponse, TrashedSessionResponse


def test_is_flicker_absent_from_session_response():
    """is_flicker is an internal bot-owned flag and must never leak to the API.

    The bot writes flicker rows; the API filters them at SELECT (visible_session).
    The flag itself is not part of the response contract.
    """
    assert "is_flicker" not in SessionResponse.model_fields
    assert "is_flicker" not in TrashedSessionResponse.model_fields
