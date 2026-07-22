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

    # --- Auth (JWT) ---
    # Insecure default so `docker-dev.sh` / native dev work with zero config.
    # Any real deployment MUST override this via the JWT_SECRET_KEY env var.
    jwt_secret_key: str = Field(default="dev-only-insecure-secret-change-me")
    jwt_algorithm: str = Field(default="HS256")
    access_token_expire_minutes: int = Field(default=60)

    # --- Token encryption (GitHub access tokens at rest) ---
    # A real, valid Fernet key so local dev works with zero config - but it's
    # public (checked into this repo), so it provides zero real
    # confidentiality. Any real deployment MUST override this via
    # TOKEN_ENCRYPTION_KEY (generate with `Fernet.generate_key()`).
    token_encryption_key: str = Field(default="7pLY9C3PlWFCWMtvlkhNSMWreEmwwM-oTidOaU-_dmk=")

    # --- Frontend (for post-OAuth redirects) ---
    frontend_base_url: str = Field(default="http://localhost:5173")

    # --- GitHub OAuth App (for "Connect GitHub", not login) ---
    # None until the user configures their own personal GitHub OAuth App -
    # see docs/setup.md. Never set these to a company-owned app's
    # credentials (see docs/adr/0006-github-integration.md).
    github_client_id: str | None = Field(default=None)
    github_client_secret: str | None = Field(default=None)
    github_oauth_redirect_uri: str = Field(default="http://localhost:8000/api/v1/github/callback")
    # Shared secret configured on the GitHub webhook itself; verifies
    # POST /api/v1/webhooks/github deliveries actually came from GitHub.
    github_webhook_secret: str | None = Field(default=None)

    # --- Neo4j (architecture graph storage — see app/graph) ---
    neo4j_uri: str = Field(default="bolt://localhost:7687")
    neo4j_user: str = Field(default="neo4j")
    neo4j_password: str = Field(default="changeguard-dev")

    # --- Indexer (Phase 6: Architecture Discovery Engine) ---
    # Where repositories are shallow-cloned for scanning; always cleaned up
    # after each indexing run, success or failure.
    indexer_clone_root: str = Field(default="/tmp/changeguard-indexer")

    # --- Future integrations (unused until their adapters are implemented) ---
    jira_base_url: str | None = Field(default=None)
    jira_api_token: str | None = Field(default=None)
    ai_engine_api_key: str | None = Field(default=None)


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance so env parsing happens once."""
    return Settings()
