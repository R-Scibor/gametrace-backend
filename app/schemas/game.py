from typing import Optional

from pydantic import BaseModel

from app.models.game import CoverSource, EnrichmentStatus


class GameResponse(BaseModel):
    id: int
    primary_name: str
    cover_image_url: Optional[str] = None
    cover_source: CoverSource
    enrichment_status: EnrichmentStatus
    is_ignored: bool = False
    is_accepted: Optional[bool] = None

    model_config = {"from_attributes": True}


class GameListResponse(BaseModel):
    total: int
    items: list[GameResponse]


class CoverUpload(BaseModel):
    image_base64: str
    extension: str = "jpg"


class GameResolveOut(BaseModel):
    game_id: int
    name: str
