from datetime import datetime

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.game import CoverSource, EnrichmentStatus


class GameResponse(BaseModel):
    id: int
    primary_name: str
    cover_image_url: str | None = None
    cover_source: CoverSource
    enrichment_status: EnrichmentStatus
    is_ignored: bool = False
    is_accepted: bool | None = None
    total_seconds: int = 0
    last_played: datetime | None = None

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
    cover_image_url: str | None = None
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
    year: int | None = None
    cover_url: str | None = None
    score: float


class GameCreateRequest(BaseModel):
    """Two modes, exactly one must be active:
    - igdb_id mode: igdb_id is set (integer).
    - unrecognized mode: unrecognized=True AND name is non-empty/non-blank.
    Optional query — only honoured (stored as a GameAlias) in igdb_id mode.
    """
    igdb_id: int | None = None
    name: str | None = None
    unrecognized: bool = False
    query: str | None = None

    @field_validator("name", "query", mode="before")
    @classmethod
    def _trim(cls, v: object) -> object:
        """Trim at the boundary so "  Foo  " and "Foo" are one catalog row and
        one alias, not two. A value that is only whitespace becomes None, which
        _require_exactly_one_mode then rejects for `name` (422) and the handler
        reads as "no query" for `query`."""
        if isinstance(v, str):
            return v.strip() or None
        return v

    @model_validator(mode="after")
    def _require_exactly_one_mode(self) -> "GameCreateRequest":
        has_igdb = self.igdb_id is not None
        has_unrecognized = self.unrecognized is True and bool(
            self.name and self.name.strip()
        )
        if has_igdb and (self.unrecognized or self.name is not None):
            raise ValueError(
                "Provide either igdb_id OR (unrecognized=true + name), not both."
            )
        if not has_igdb and not has_unrecognized:
            raise ValueError(
                "Provide igdb_id, or set unrecognized=true with a non-blank name."
            )
        return self
