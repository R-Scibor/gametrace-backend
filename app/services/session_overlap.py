"""Detect the session range-exclusion violation (SQLSTATE 23P01)."""

from sqlalchemy.exc import IntegrityError

OVERLAP_CONSTRAINT = "excl_game_sessions_no_overlap"


def is_session_overlap(exc: IntegrityError) -> bool:
    """True when Postgres rejected a live non-flicker range that overlaps."""
    orig = exc.orig
    if getattr(orig, "sqlstate", None) != "23P01":
        return False
    return OVERLAP_CONSTRAINT in str(orig)
