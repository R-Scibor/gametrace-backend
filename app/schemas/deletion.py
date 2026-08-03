from datetime import datetime

from pydantic import BaseModel


class DeletionStatusResponse(BaseModel):
    deletion_requested_at: datetime
    purge_at: datetime
    days_left: int
