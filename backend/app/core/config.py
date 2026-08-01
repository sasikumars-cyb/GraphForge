"""Application settings, loaded from environment variables.

A single `Settings` object is the only place in the codebase allowed to read
`os.environ` — every other module receives configuration through this.
"""

from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Fields whose default value is a real, working credential — safe for local
# dev (documented at each field below) but a full compromise if ever left
# unrotated in production: `jwt_secret_key` forges valid auth tokens for any
# user, `token_encryption_key` decrypts every GitHub token at rest,
# `neo4j_password` is the actual Neo4j credential this app connects with.
# `_reject_insecure_defaults_in_production` below is the fail-fast guard —
# without it, an operator who forgets to override one of these in production
# gets a silently-broken security boundary instead of a startup error.
_INSECURE_DEFAULTS: dict[str, str] = {
    "jwt_secret_key": "dev-only-insecure-secret-change-me",
    "token_encryption_key": "7pLY9C3PlWFCWMtvlkhNSMWreEmwwM-oTidOaU-_dmk=",
    "neo4j_password": "graphforge-dev",
}


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

    google_client_id: str | None = Field(default=None)
    google_client_secret: str | None = Field(default=None)
    google_oauth_redirect_uri: str = Field(
        default="http://localhost:8000/api/v1/google-drive/callback"
    )
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

    # --- Local repository indexing (see app.services.local_repository_service) ---
    # Root a user-submitted repository path is resolved and bound against
    # (defense against path traversal/symlink escape - see that service's
    # own validation). None (default) disables the feature entirely rather
    # than silently no-op-ing on an unset root. Under Docker, this is the
    # container-side mount point of an operator-chosen host directory (see
    # docker/docker-compose.local-repos.yml); running natively
    # (scripts/dev.sh), it can point anywhere on the host directly since
    # there's no container boundary.
    local_repos_root: str | None = Field(default=None)

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
    # Confluence) was assumed to require OAuth 2.1 only — confirmed
    # empirically (see the session that added this default) that it also
    # accepts a plain bearer-token API key for authentication; the only
    # real-world failure hit was an org-level "API token access" toggle an
    # Atlassian admin controls, unrelated to the transport itself. Jira
    # already prefers MCP but falls back to REST on failure (see
    # JiraTool.execute()); Confluence document discovery has no REST path
    # at all — it's MCP-only (Teamwork Graph — see
    # app.agents.planning.confluence_context's module docstring for why),
    # so this default is the only way it ever actually works. Defaulting
    # this on is safe: an org that hasn't granted API token access just
    # falls back to (Jira's working) REST, or (for Confluence) stays
    # exactly as non-functional as before. Override via env if you have a
    # self-hosted/compatible MCP server.
    github_mcp_default_server_url: str = Field(default="https://api.githubcopilot.com/mcp/")
    jira_mcp_default_server_url: str | None = Field(
        default="https://mcp.atlassian.com/v1/mcp/authv2"
    )
    confluence_mcp_default_server_url: str | None = Field(
        default="https://mcp.atlassian.com/v1/mcp/authv2"
    )

    # Off by default: this adds one extra bounded LLM call to every planning
    # run whose prompt has no deterministic Jira/Confluence/GitHub/repository
    # reference (see app.context_pipeline.discovery), which is the common
    # case for a freeform request — enabling it by default would silently
    # add latency/cost to most runs rather than only the ones that actually
    # benefit. Turn on once the recommendation quality has been evaluated.
    enable_context_discovery: bool = Field(default=False)

    @model_validator(mode="after")
    def _reject_insecure_defaults_in_production(self) -> "Settings":
        """Fail fast rather than run production on a publicly-known secret.

        `environment` itself has an insecure default ("development"), so
        this only fires once an operator has explicitly set
        `ENVIRONMENT=production` — at that point, silence is worse than a
        crash: a misconfigured production deployment would otherwise look
        healthy while being fully compromisable by anyone who has ever
        seen this repository (see _INSECURE_DEFAULTS' docstring above).
        """
        if self.environment == "production":
            offending = [
                field
                for field, insecure_value in _INSECURE_DEFAULTS.items()
                if getattr(self, field) == insecure_value
            ]
            if offending:
                raise ValueError(
                    "Refusing to start with ENVIRONMENT=production while these "
                    f"settings still hold their insecure default value: {', '.join(offending)}. "
                    "Set real values via environment variables (see backend/.env.example) "
                    "before deploying."
                )
        return self


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance so env parsing happens once."""
    return Settings()
