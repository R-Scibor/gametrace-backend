from pydantic import BaseModel, Field, field_validator


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=100)
    timezone: str = Field(default="UTC", max_length=64)


class LoginResponse(BaseModel):
    token: str
    discord_id: str
    username: str
    timezone: str
    is_admin: bool
    needs_server_join: bool = False


class LinkCodeRequest(BaseModel):
    code: str
    timezone: str = Field(default="UTC", max_length=64)

    @field_validator("code")
    @classmethod
    def _code_must_be_six_digits(cls, v: str) -> str:
        v = "".join(v.split())
        if len(v) != 6 or not v.isdigit():
            raise ValueError("code must be exactly 6 digits")
        return v


class DiscordCallbackRequest(BaseModel):
    code: str = Field(..., min_length=1)
    code_verifier: str = Field(..., min_length=1)
    redirect_uri: str = Field(..., min_length=1)
