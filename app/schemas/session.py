from datetime import datetime

from pydantic import BaseModel, ConfigDict, model_validator

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
    model_config = ConfigDict(extra="forbid")

    end_time: datetime | None = None


class GameBrief(BaseModel):
    id: int
    primary_name: str
    cover_image_url: str | None = None

    model_config = {"from_attributes": True}


class SessionResponse(BaseModel):
    id: int
    game_id: int
    game: GameBrief
    start_time: datetime
    end_time: datetime | None = None
    duration_seconds: int | None = None
    status: SessionStatus
    source: SessionSource
    notes: str | None = None
    created_at: datetime
    deleted_at: datetime | None = None

    model_config = {"from_attributes": True}


class TrashedSessionResponse(SessionResponse):
    purges_at: datetime


class ConflictResponse(BaseModel):
    detail: str
    conflicting_session: SessionResponse
