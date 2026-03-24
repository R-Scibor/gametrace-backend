from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    redis_url: str

    discord_bot_token: str = ""
    openai_api_key: str = ""
    gemini_api_key: str = ""

    session_token_expire_days: int = 30


settings = Settings()
