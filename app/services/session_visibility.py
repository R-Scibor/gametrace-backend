from sqlalchemy.sql.elements import ColumnElement

from app.models.session import GameSession


def visible_session() -> list[ColumnElement[bool]]:
    """Filter clauses that hide soft-deleted and flicker sessions. Spread with * into .where().

    Do NOT use this in the bot's stitch-candidate lookup — that query intentionally
    selects flicker rows in order to reopen them.
    """
    return [GameSession.deleted_at.is_(None), GameSession.is_flicker.is_(False)]
