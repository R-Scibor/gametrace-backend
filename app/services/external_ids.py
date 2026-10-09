"""Stored form of games.external_api_id.

Callers format ids through these functions. A bare number is never a match.
"""

from sqlalchemy.exc import IntegrityError

EXTERNAL_API_ID_UNIQUE = "uq_games_external_api_id"


def _positive_int(value: int, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{label} must be an int >= 1")
    return value


def igdb_external_id(igdb_id: int) -> str:
    return f"igdb:{_positive_int(igdb_id, 'igdb_id')}"


def steam_external_id(app_id: int) -> str:
    return f"steam:{_positive_int(app_id, 'app_id')}"


def is_external_id_conflict(exc: IntegrityError) -> bool:
    """True when Postgres rejected a duplicate non-null external_api_id."""
    orig = exc.orig
    if getattr(orig, "sqlstate", None) != "23505":
        return False
    if getattr(orig, "constraint_name", None) == EXTERNAL_API_ID_UNIQUE:
        return True
    return EXTERNAL_API_ID_UNIQUE in str(orig)
