"""Application settings, loaded from environment variables.

A single `Settings` object is the only place in the codebase allowed to read
`os.environ` — every other module receives configuration through this.
"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Application ---
    app_name: str = "ChangeGuard"
    environment: str = Field(default="development")
    debug: bool = Field(default=True)
    api_v1_prefix: str = "/api/v1"

    # --- CORS ---
    cors_allow_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    # --- Database ---
    database_url: str = Field(
        default="postgresql+asyncpg://changeguard:changeguard@localhost:5432/changeguard",
        description="Async SQLAlchemy connection string (asyncpg driver).",
    )
    database_echo: bool = Field(default=False)

    # --- Future integrations (unused until their adapters are implemented) ---
    github_token: str | None = Field(default=None)
    jira_base_url: str | None = Field(default=None)
    jira_api_token: str | None = Field(default=None)
    neo4j_uri: str | None = Field(default=None)
    ai_engine_api_key: str | None = Field(default=None)


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance so env parsing happens once."""
    return Settings()
