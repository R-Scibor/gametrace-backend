from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    redis_url: str

    discord_bot_token: str = ""
    openai_api_key: str = ""
    gcp_project: str = ""
    gcp_location: str = "us-central1"
    igdb_client_id: str = ""
    igdb_client_secret: str = ""

    firebase_credentials_path: str = ""

    sentry_dsn: str = ""
    sentry_environment: str = "homelab"

    flower_basic_auth: str = ""

    session_token_expire_days: int = 30


settings = Settings()
