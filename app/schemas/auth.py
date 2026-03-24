from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    discord_id: str = Field(..., min_length=1, max_length=32)
    username: str = Field(..., min_length=1, max_length=100)
    timezone: str = Field(default="UTC", max_length=64)


class LoginResponse(BaseModel):
    token: str
    discord_id: str
    username: str
    timezone: str
