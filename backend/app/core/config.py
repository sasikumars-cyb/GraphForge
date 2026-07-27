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
    app_name: str = "GraphForge"
    environment: str = Field(default="development")
    debug: bool = Field(default=True)
    api_v1_prefix: str = "/api/v1"

    # --- CORS ---
    cors_allow_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    # --- Database ---
    database_url: str = Field(
        default="postgresql+asyncpg://graphforge:graphforge@localhost:5432/graphforge",
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
    neo4j_password: str = Field(default="graphforge-dev")

    # --- Indexer (Phase 6: Architecture Discovery Engine) ---
    # Where repositories are shallow-cloned for scanning; always cleaned up
    # after each indexing run, success or failure.
    indexer_clone_root: str = Field(default="/tmp/graphforge-indexer")

    # --- Version control provider (local demo support) ---
    # "github" (default, unchanged production behavior) or "local_git" - an
    # explicit opt-in used only by the local demo environment (see
    # demo/DEMO_GUIDE.md), where "pull requests" are branches on disk
    # instead of real GitHub PRs.
    vcs_provider: str = Field(default="github")
    demo_repositories_root: str = Field(default="../demo/repositories")

    # --- AI Provider ---
    ai_provider: str = Field(default="openai")
    openai_api_key: str | None = Field(default=None)
    openai_model: str = Field(default="gpt-4o")
    openai_temperature: float = Field(default=0.2)
    openai_max_tokens: int = Field(default=4096)

    # --- Groq (free-tier alternative - OpenAI-compatible Chat Completions
    # API, no billing required; see console.groq.com) ---
    groq_api_key: str | None = Field(default=None)
    groq_model: str = Field(default="llama-3.3-70b-versatile")

    # --- Gemini (Google Generative Language API) ---
    gemini_api_key: str | None = Field(default=None)
    gemini_model: str = Field(default="gemini-3.6-flash")
    # Higher than openai_max_tokens: structured JSON responses (e.g. the
    # Testing agent's test plan) were getting cut off mid-string at 4096,
    # producing invalid JSON - see the truncation this default now avoids.
    gemini_max_tokens: int = Field(default=8192)

    # --- Amazon Bedrock (AWS credential chain — no API key stored) ---
    bedrock_region: str = Field(default="us-east-1")
    bedrock_model: str = Field(default="us.anthropic.claude-sonnet-4-20250514")
    # Higher than openai_max_tokens for the same reason gemini_max_tokens is
    # (truncated JSON), plus a hybrid-reasoning model (e.g. Claude Haiku 4.5)
    # spends part of this same budget on its own reasoning trace before ever
    # emitting the final answer - too low a cap can consume the entire
    # budget on reasoning and return empty text, which is exactly what
    # happened at the previous 4096 default (inherited from openai_max_tokens
    # via the resolver's fallback, before this field existed).
    bedrock_max_tokens: int = Field(default=16384)

    # --- Future integrations (unused until their adapters are implemented) ---
    jira_base_url: str | None = Field(default=None)
    jira_api_token: str | None = Field(default=None)
    ai_engine_api_key: str | None = Field(default=None)

    # --- Known MCP server endpoints, one default per source type ---
    # These are the *official* hosted MCP servers for each vendor - they
    # change rarely, so they belong in config rather than being typed into
    # every Knowledge Connection. A Knowledge Connection's existing API
    # key/token is reused as the MCP bearer credential (see
    # app.tools.setup.sync_knowledge_connection_to_tool); nothing new is
    # asked of the user in the Integrations UI.
    #
    # GitHub's hosted MCP server accepts a plain PAT as a bearer token, so
    # it is safe to default on. Atlassian's hosted MCP server (Jira/
    # Confluence) requires OAuth 2.1, which this app's MCP client does not
    # yet implement — defaulting it here would silently break working REST
    # connections the moment an operator enabled MCP, so it is left unset
    # until either OAuth support is added or an operator points it at a
    # self-hosted/compatible bearer-auth Jira or Confluence MCP server.
    github_mcp_default_server_url: str = Field(default="https://api.githubcopilot.com/mcp/")
    jira_mcp_default_server_url: str | None = Field(default=None)
    confluence_mcp_default_server_url: str | None = Field(default=None)


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance so env parsing happens once."""
    return Settings()
