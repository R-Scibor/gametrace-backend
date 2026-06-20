from sqlalchemy import and_, or_

from app.models.game import EnrichmentStatus, Game, UserGamePreference


def library_visible_filter():
    """Games/sessions visible in the main library and stats aggregates."""
    return and_(
        or_(
            UserGamePreference.is_ignored.is_(None),
            UserGamePreference.is_ignored.is_(False),
        ),
        or_(
            Game.enrichment_status != EnrichmentStatus.NEEDS_REVIEW,
            UserGamePreference.is_accepted.is_(True),
        ),
    )


def review_inbox_filter():
    """Unrecognized tab: NEEDS_REVIEW stubs the user has not accepted yet."""
    return and_(
        or_(
            UserGamePreference.is_ignored.is_(None),
            UserGamePreference.is_ignored.is_(False),
        ),
        or_(
            UserGamePreference.is_accepted.is_(None),
            UserGamePreference.is_accepted.is_(False),
        ),
    )


def ignored_only_filter():
    """Hidden library tab: games the user has marked ignored."""
    return UserGamePreference.is_ignored.is_(True)