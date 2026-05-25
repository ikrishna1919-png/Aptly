from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = Field(
        default="postgresql+psycopg://aptly:aptly@localhost:5432/aptly",
        alias="DATABASE_URL",
    )
    cors_origins: str = Field(default="http://localhost:3000", alias="CORS_ORIGINS")
    environment: str = Field(default="development", alias="ENVIRONMENT")

    # Rolling-window size for the job feed. Anything older than this is
    # deleted on each ingest pass.
    hours_window: int = Field(default=48, alias="HOURS_WINDOW")

    # Shared secret required by the admin ingest endpoint. The scheduled
    # GitHub Actions workflow sends it in the X-Admin-Token header.
    admin_token: str = Field(default="", alias="ADMIN_TOKEN")

    # Anthropic API key for resume tailoring (Phase 4). Empty string puts the
    # tailoring endpoints into "demo mode" — they return deterministic mock
    # data so nothing crashes when the key isn't configured.
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def has_anthropic_key(self) -> bool:
        return bool(self.anthropic_api_key.strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()
