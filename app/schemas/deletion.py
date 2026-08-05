from datetime import datetime

from pydantic import BaseModel


class DeletionStatusResponse(BaseModel):
    deletion_requested_at: datetime
    purge_at: datetime
    days_left: int


class PendingDeletion(BaseModel):
    """Carried on LoginResponse when the authenticating account is scheduled
    for deletion, so the client can offer a cancel dialog. None/absent for
    normal accounts — logging in never cancels the deletion by itself."""

    deletion_requested_at: datetime
    purge_at: datetime
    days_left: int
