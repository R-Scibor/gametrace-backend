from typing import Optional

from pydantic import BaseModel, Field


class PreferenceUpdate(BaseModel):
    is_ignored: bool = False
    custom_tag: Optional[str] = Field(default=None, max_length=64)


class PreferenceResponse(BaseModel):
    game_id: int
    is_ignored: bool
    custom_tag: Optional[str] = None
