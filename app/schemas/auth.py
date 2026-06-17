from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=100)
    timezone: str = Field(default="UTC", max_length=64)


class LoginResponse(BaseModel):
    token: str
    discord_id: str
    username: str
    timezone: str
    needs_server_join: bool = False


class DiscordCallbackRequest(BaseModel):
    code: str = Field(..., min_length=1)
    code_verifier: str = Field(..., min_length=1)
    redirect_uri: str = Field(..., min_length=1)
