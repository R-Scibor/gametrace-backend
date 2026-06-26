from typing import Optional

from pydantic import BaseModel, Field

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


class GameSuggestItem(BaseModel):
    game_id: int
    primary_name: str
    cover_image_url: Optional[str] = None
    enrichment_status: EnrichmentStatus
    score: float


class GameSuggestResponse(BaseModel):
    total: int
    items: list[GameSuggestItem]


class GameMatchRequest(BaseModel):
    query: str = Field(..., min_length=1)


class IGDBCandidateOut(BaseModel):
    igdb_id: int
    name: str
    year: Optional[int] = None
    cover_url: Optional[str] = None
    score: float
