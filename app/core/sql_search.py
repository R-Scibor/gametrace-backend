"""Literal substring search over text columns.

A user-typed `%` or `_` means the character, not a SQL wildcard, so every
ILIKE-backed search box has to escape its input and pass the same ESCAPE clause.
Forgetting either half is silent — the query still runs, it just matches the
wrong rows — so callers get one helper that does both rather than the raw
`ilike(f"%{q}%")` it replaces.
"""
from sqlalchemy.orm import InstrumentedAttribute
from sqlalchemy.sql.elements import ColumnElement

ESCAPE_CHAR = "\\"


def escape_like(value: str) -> str:
    """Escape LIKE/ILIKE metacharacters so a typed `%`/`_` matches literally."""
    return (
        value.replace(ESCAPE_CHAR, ESCAPE_CHAR * 2)
        .replace("%", f"{ESCAPE_CHAR}%")
        .replace("_", f"{ESCAPE_CHAR}_")
    )


def ilike_contains(
    column: InstrumentedAttribute[str], value: str
) -> ColumnElement[bool]:
    """Case-insensitive substring match where `%`/`_` in *value* are literal."""
    return column.ilike(f"%{escape_like(value)}%", escape=ESCAPE_CHAR)
