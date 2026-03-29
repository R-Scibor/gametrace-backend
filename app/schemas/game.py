from typing import Optional

from pydantic import BaseModel

from app.models.game import CoverSource, EnrichmentStatus


class GameResponse(BaseModel):
    id: int
    primary_name: str
    cover_image_url: Optional[str] = None
    cover_source: CoverSource
    enrichment_status: EnrichmentStatus

    model_config = {"from_attributes": True}


class CoverUpload(BaseModel):
    image_base64: str
    extension: str = "jpg"
