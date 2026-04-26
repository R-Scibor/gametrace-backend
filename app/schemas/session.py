from datetime import datetime
from typing import Optional

from pydantic import BaseModel, model_validator

from app.models.session import SessionSource, SessionStatus


class SessionCreate(BaseModel):
    game_id: int
    start_time: datetime
    end_time: datetime

    @model_validator(mode="after")
    def check_times(self):
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be after start_time")
        return self


class SessionPatch(BaseModel):
    end_time: Optional[datetime] = None
    discard: bool = False


class GameBrief(BaseModel):
    id: int
    primary_name: str
    cover_image_url: Optional[str] = None

    model_config = {"from_attributes": True}


class SessionResponse(BaseModel):
    id: int
    game_id: int
    game: GameBrief
    start_time: datetime
    end_time: Optional[datetime] = None
    duration_seconds: Optional[int] = None
    status: SessionStatus
    source: SessionSource
    notes: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class ConflictResponse(BaseModel):
    detail: str
    conflicting_session: SessionResponse
