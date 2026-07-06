from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    redis_url: str

    discord_bot_token: str = ""
    openai_api_key: str = ""
    gcp_project: str = ""
    gcp_location: str = "us-central1"
    gemini_model: str = "gemini-3-flash-preview"
    default_timezone: str = "Europe/Warsaw"
    igdb_client_id: str = ""
    igdb_client_secret: str = ""

    discord_client_id: str = ""
    discord_client_secret: str = ""
    discord_oauth_redirect_uris: str = ""  # comma-separated allowlist
    discord_guild_ids: str = ""  # comma-separated; membership required for presence tracking

    firebase_credentials_path: str = ""

    sentry_dsn: str = ""
    sentry_environment: str = "homelab"

    flower_basic_auth: str = ""

    link_code_secret: str = ""  # HMAC key for /login codes; empty disables the feature
    dev_login_secret: str = ""  # shared secret gating name-only /auth/login; empty disables it
    trusted_proxy_ips: str = ""  # comma-separated IPs allowed to set X-Forwarded-For

    session_token_expire_days: int = 30
    trash_retention_days: int = 7

    # Debounce the per-request last_active / sliding-expiry write in get_current_user:
    # only persist when last_active is older than this. 0 restores per-request writes.
    token_activity_debounce_minutes: int = 10

    # App-side backstop cap on request body size (Content-Length). The primary
    # control is nginx client_max_body_size at the NPM edge; keep this in sync.
    # 10 MiB covers cover uploads (base64) and voice clips with headroom.
    max_request_body_bytes: int = 10 * 1024 * 1024

    # How long a presence dropout can be (seconds) and still count as continuous play.
    session_stitch_window_seconds: int = 180
    # Sessions shorter than this (seconds) are treated as junk flickers.
    session_short_flicker_seconds: int = 180
    # How long a flicker-flagged row survives (seconds) before the GC purges it.
    session_flicker_gc_margin_seconds: int = 86400

    app_version: str = "dev"
    git_sha: str = "dev"
    build_time: str = "unknown"

    @property
    def discord_redirect_uri_allowlist(self) -> set[str]:
        return {u.strip() for u in self.discord_oauth_redirect_uris.split(",") if u.strip()}

    @property
    def discord_guild_id_set(self) -> set[str]:
        return {g.strip() for g in self.discord_guild_ids.split(",") if g.strip()}

    @property
    def trusted_proxy_ip_set(self) -> set[str]:
        return {ip.strip() for ip in self.trusted_proxy_ips.split(",") if ip.strip()}

    @model_validator(mode="after")
    def _gc_margin_exceeds_stitch_window(self) -> "Settings":
        if self.session_flicker_gc_margin_seconds <= self.session_stitch_window_seconds:
            raise ValueError(
                "session_flicker_gc_margin_seconds must be strictly greater than "
                "session_stitch_window_seconds — the GC must never delete a row "
                "still eligible to be a stitch target "
                f"(got gc_margin={self.session_flicker_gc_margin_seconds}, "
                f"stitch_window={self.session_stitch_window_seconds})"
            )
        return self


settings = Settings()
