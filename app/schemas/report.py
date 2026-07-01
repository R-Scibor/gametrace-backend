from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel


class ReportContext(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    screen: str
    platform: str
    os_version: str | int
    app_version: str


class ReportCreate(BaseModel):
    message: str = Field(max_length=4000)
    context: ReportContext

    @field_validator("message")
    @classmethod
    def _message_not_blank(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("message must not be empty")
        return stripped


class ReportResponse(BaseModel):
    id: int
    created_at: datetime
