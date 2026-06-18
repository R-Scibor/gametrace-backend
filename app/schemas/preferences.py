from typing import Optional

from pydantic import BaseModel, Field


class PreferenceUpdate(BaseModel):
    is_ignored: bool = False
    is_accepted: Optional[bool] = None
    custom_tag: Optional[str] = Field(default=None, max_length=64)


class PreferenceResponse(BaseModel):
    game_id: int
    is_ignored: bool
    is_accepted: Optional[bool] = None
    custom_tag: Optional[str] = None
