from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator

from app.models.session import SessionSource, SessionStatus


def _require_offset(value: datetime) -> datetime:
    """Reject a wall time with no offset. Callers send Z or a numeric offset."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must include a timezone offset")
    return value


class SessionCreate(BaseModel):
    game_id: int
    start_time: datetime
    end_time: datetime

    @field_validator("start_time", "end_time")
    @classmethod
    def _times_are_aware(cls, value: datetime) -> datetime:
        return _require_offset(value)


class SessionPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    end_time: datetime | None = None

    @field_validator("end_time")
    @classmethod
    def _end_time_is_aware(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return _require_offset(value)


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


class ConflictEnvelope(BaseModel):
    """HTTP body is this object. ConflictResponse is the nested detail."""

    detail: ConflictResponse
