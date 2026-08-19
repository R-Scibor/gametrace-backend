from datetime import datetime

from pydantic import BaseModel


class DeletionStatusResponse(BaseModel):
    deletion_requested_at: datetime
    purge_at: datetime
    days_left: int
    grace_days: int


class PendingDeletion(BaseModel):
    """Carried on LoginResponse when the authenticating account is scheduled
    for deletion, so the client can offer a cancel dialog. None/absent for
    normal accounts — logging in never cancels the deletion by itself."""

    deletion_requested_at: datetime
    purge_at: datetime
    days_left: int
    grace_days: int


class AccountDeletionState(BaseModel):
    """`GET /profile/me/deletion`. Not-scheduled is a normal state, not an error,
    so the three deletion fields are null rather than the response being a 404.

    `grace_days` is always present: the window this account was scheduled under
    when one is pending, otherwise the window that would apply if it scheduled
    one now.
    """

    deletion_requested_at: datetime | None
    purge_at: datetime | None
    days_left: int | None
    grace_days: int
