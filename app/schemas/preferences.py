
from pydantic import BaseModel, Field


class PreferenceUpdate(BaseModel):
    is_ignored: bool = False
    is_accepted: bool | None = None
    custom_tag: str | None = Field(default=None, max_length=64)


class PreferenceResponse(BaseModel):
    game_id: int
    is_ignored: bool
    is_accepted: bool | None = None
    custom_tag: str | None = None
