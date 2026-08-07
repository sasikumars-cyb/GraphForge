# 11 — Configuration Reference

## Purpose

Every configuration value GraphForge reads, exactly as declared in `backend/app/core/config.py`'s `Settings` class — the **only** module in the codebase permitted to read `os.environ` directly. Cross-referenced against `backend/.env.example` (which has drifted slightly — it predates the Bedrock/MCP fields; where they disagree, this document follows `config.py`, the actual implementation).

## How configuration resolves

`Settings` is a `pydantic-settings.BaseSettings` subclass, loaded once and cached (`get_settings()`, `@lru_cache`). Precedence, standard pydantic-settings behavior: **explicit environment variable** (case-insensitive, per `case_sensitive=False`) → `.env` file (`env_file=".env"`, local dev convenience only) → the field's declared default. Unknown environment variables are ignored (`extra="ignore"`), not rejected.

**Production safety valve** (do not remove): `_reject_insecure_defaults_in_production`, a `model_validator`, raises at startup if `environment == "production"` and `jwt_secret_key`, `token_encryption_key`, or `neo4j_password` still equal their public, checked-in dev defaults. This is the mechanism that turns "forgot to set a secret" into a loud startup crash instead of a silent vulnerability.

## Full field reference

### Application

| Field | Env var | Default | Required in prod? | Notes |
|---|---|---|---|---|
| `app_name` | `APP_NAME` | `"GraphForge"` | No | Cosmetic |
| `environment` | `ENVIRONMENT` | `"development"` | **Yes** — set to `"production"` | Activates the insecure-defaults guard |
| `debug` | `DEBUG` | `true` | **Yes** — set to `false` | The app is built with `debug=False` in production so the 3-tier exception handler chain runs consistently (`04_SECURITY.md`) |
| `api_v1_prefix` | `API_V1_PREFIX` | `"/api/v1"` | No | Should not change without also updating the ALB's path-based routing rule (`03_NETWORKING.md`) |

### CORS

| Field | Env var | Default | Required in prod? | Notes |
|---|---|---|---|---|
| `cors_allow_origins` | `CORS_ALLOW_ORIGINS` | `["http://localhost:5173"]` | No, under the recommended ALB same-origin routing (`03_NETWORKING.md`) — becomes required (set to the real production domain, never `["*"]`) only if the CloudFront-split-origin alternative is adopted instead | JSON array syntax in the env var, e.g. `CORS_ALLOW_ORIGINS=["https://graphforge.example.com"]` |

### Database

| Field | Env var | Default | Required in prod? | Notes |
|---|---|---|---|---|
| `database_url` | `DATABASE_URL` | `postgresql+asyncpg://graphforge:graphforge@localhost:5432/graphforge` | **Yes — from Secrets Manager** | Async SQLAlchemy connection string, `asyncpg` driver required |
| `database_echo` | `DATABASE_ECHO` | `false` | No | SQL statement logging — never enable in production (verbose, may log parameter values) |

### Auth (JWT)

| Field | Env var | Default | Required in prod? | Notes |
|---|---|---|---|---|
| `jwt_secret_key` | `JWT_SECRET_KEY` | `dev-only-insecure-secret-change-me` | **Yes — from Secrets Manager** | Guarded by the insecure-defaults validator |
| `jwt_algorithm` | `JWT_ALGORITHM` | `"HS256"` | No | |
| `access_token_expire_minutes` | `ACCESS_TOKEN_EXPIRE_MINUTES` | `60` | No | No refresh-token mechanism exists — this is the full session lifetime |

### Token encryption

| Field | Env var | Default | Required in prod? | Notes |
|---|---|---|---|---|
| `token_encryption_key` | `TOKEN_ENCRYPTION_KEY` | `7pLY9C3PlWFCWMtvlkhNSMWreEmwwM-oTidOaU-_dmk=` (a real, valid, but public Fernet key) | **Yes — from Secrets Manager** | Guarded by the insecure-defaults validator. Generate with `Fernet.generate_key()`. See `06_SECRETS.md` for why rotating this is destructive without a migration that doesn't exist yet |

### Frontend

| Field | Env var | Default | Required in prod? | Notes |
|---|---|---|---|---|
| `frontend_base_url` | `FRONTEND_BASE_URL` | `http://localhost:5173` | **Yes** — the real production domain | Used for post-OAuth redirects |

### GitHub OAuth ("Connect GitHub" — not login)

| Field | Env var | Default | Required in prod? | Notes |
|---|---|---|---|---|
| `github_client_id` | `GITHUB_CLIENT_ID` | `None` | Only if GitHub repo connection is enabled | Must be **your own** GitHub OAuth App — never a shared one (`docs/adr/0006-github-integration.md`) |
| `github_client_secret` | `GITHUB_CLIENT_SECRET` | `None` | Only if enabled | From Secrets Manager if set |
| `github_oauth_redirect_uri` | `GITHUB_OAUTH_REDIRECT_URI` | `http://localhost:8000/api/v1/github/callback` | Only if enabled | Must match the production domain and the URI registered on the GitHub OAuth App |
| `github_webhook_secret` | `GITHUB_WEBHOOK_SECRET` | `None` | Only if GitHub webhooks are configured | Verifies `POST /webhooks/github` deliveries via HMAC-SHA256 |

### Neo4j

| Field | Env var | Default | Required in prod? | Notes |
|---|---|---|---|---|
| `neo4j_uri` | `NEO4J_URI` | `bolt://localhost:7687` | **Yes** | Use `neo4j+s://` for Aura (TLS-required) |
| `neo4j_user` | `NEO4J_USER` | `"neo4j"` | **Yes** | |
| `neo4j_password` | `NEO4J_PASSWORD` | `graphforge-dev` | **Yes — from Secrets Manager** | Guarded by the insecure-defaults validator |

### Indexer

| Field | Env var | Default | Required in prod? | Notes |
|---|---|---|---|---|
| `indexer_clone_root` | `INDEXER_CLONE_ROOT` | `/tmp/graphforge-indexer` | No | Ephemeral, always cleaned up per job — Fargate's ephemeral task storage is sufficient, no persistent volume needed |

### Version control provider (demo support)

| Field | Env var | Default | Required in prod? | Notes |
|---|---|---|---|---|
| `vcs_provider` | `VCS_PROVIDER` | `"github"` | No — never set to `"local_git"` in production | `"local_git"` is an explicit opt-in for `demo/DEMO_GUIDE.md`'s environment only |
| `demo_repositories_root` | `DEMO_REPOSITORIES_ROOT` | `../demo/repositories` | No | Only relevant when `vcs_provider="local_git"` |

### AI Provider — environment-level fallback tier

See `13_AI_PROVIDER_CONFIGURATION.md` for how this tier fits into the full resolution order.

| Field | Env var | Default | Required in prod? | Notes |
|---|---|---|---|---|
| `ai_provider` | `AI_PROVIDER` | `"openai"` — **recommend changing to `"bedrock"`** per `10_CODE_CHANGES.md` §6.4 | No, but see the recommendation | The bottom-tier fallback if nothing else is configured anywhere |
| `openai_api_key` | `OPENAI_API_KEY` | `None` | Only if OpenAI is used at this tier | |
| `openai_model` | `OPENAI_MODEL` | `"gpt-4o"` | No | |
| `openai_temperature` | `OPENAI_TEMPERATURE` | `0.2` | No | |
| `openai_max_tokens` | `OPENAI_MAX_TOKENS` | `4096` | No | |
| `groq_api_key` | `GROQ_API_KEY` | `None` | Only if Groq is used at this tier | Free-tier, OpenAI-compatible Chat Completions API |
| `groq_model` | `GROQ_MODEL` | `"llama-3.3-70b-versatile"` | No | |
| `deepseek_api_key` | `DEEPSEEK_API_KEY` | `None` | Only if DeepSeek is used at this tier | OpenAI-compatible Chat Completions API |
| `deepseek_model` | `DEEPSEEK_MODEL` | `"deepseek-v4-flash"` | No | `"deepseek-v4-pro"` is also registered |
| `deepseek_base_url` | `DEEPSEEK_BASE_URL` | `None` | No | Overrides the official DeepSeek API URL — self-hosted/third-party OpenAI-compatible endpoints only |
| `deepseek_max_tokens` | `DEEPSEEK_MAX_TOKENS` | `16384` | No | Higher than OpenAI's default — both registered DeepSeek models (hybrid-reasoning) spend part of this budget on its reasoning trace before the final answer, same failure mode as Bedrock's hybrid-reasoning models |
| `gemini_api_key` | `GEMINI_API_KEY` | `None` | Only if Gemini is used at this tier | |
| `gemini_model` | `GEMINI_MODEL` | `"gemini-3.6-flash"` | No | |
| `gemini_max_tokens` | `GEMINI_MAX_TOKENS` | `8192` | No | Higher than OpenAI's default — structured JSON responses were truncating at 4096 |
| `bedrock_region` | `BEDROCK_REGION` | `"us-east-1"` | Recommended to set explicitly | |
| `bedrock_model` | `BEDROCK_MODEL` | `"us.anthropic.claude-sonnet-4-20250514"` | No | Must match the model ARNs granted in the Task Role's IAM policy (`05_IAM.md`) |
| `bedrock_max_tokens` | `BEDROCK_MAX_TOKENS` | `16384` | No | Higher still than Gemini's — a hybrid-reasoning model spends part of this budget on its own reasoning trace before the final answer |

**No `bedrock_api_key` field exists, deliberately** — Bedrock authenticates via `boto3`'s default AWS credential chain (ECS Task Role in production), never a stored application secret.

### Future integrations (unused until their adapters exist)

| Field | Env var | Default | Notes |
|---|---|---|---|
| `jira_base_url` | `JIRA_BASE_URL` | `None` | Legacy/env fallback — the primary path today is the Knowledge Connection UI |
| `jira_api_token` | `JIRA_API_TOKEN` | `None` | Same |
| `ai_engine_api_key` | `AI_ENGINE_API_KEY` | `None` | Unused |

### MCP server endpoints

| Field | Env var | Default | Notes |
|---|---|---|---|
| `github_mcp_default_server_url` | `GITHUB_MCP_DEFAULT_SERVER_URL` | `https://api.githubcopilot.com/mcp/` | GitHub's official hosted MCP server |
| `jira_mcp_default_server_url` | `JIRA_MCP_DEFAULT_SERVER_URL` | `https://mcp.atlassian.com/v1/mcp/authv2` | Atlassian's hosted MCP server |
| `confluence_mcp_default_server_url` | `CONFLUENCE_MCP_DEFAULT_SERVER_URL` | `https://mcp.atlassian.com/v1/mcp/authv2` | Same — Confluence's REST path is a permanent stub, so this default is the only way Confluence search works at all |

### Feature flags

| Field | Env var | Default | Notes |
|---|---|---|---|
| `enable_context_discovery` | `ENABLE_CONTEXT_DISCOVERY` | `false` | Adds one extra bounded LLM call to Planning runs with no deterministic Jira/Confluence/GitHub/repository reference — off by default to avoid silent latency/cost on the common freeform-prompt case |

## Environment variable checklist for a production deployment

Set explicitly (non-secret, in the ECS task definition's `environment` block):
```
ENVIRONMENT=production
DEBUG=false
FRONTEND_BASE_URL=https://<your-domain>
CORS_ALLOW_ORIGINS=["https://<your-domain>"]        # only if not using the ALB same-origin routing
BEDROCK_REGION=<your-region>
AI_PROVIDER=bedrock                                  # recommended per 10_CODE_CHANGES.md §6.4
GITHUB_OAUTH_REDIRECT_URI=https://<your-domain>/api/v1/github/callback   # only if GitHub connect is enabled
```

Inject from Secrets Manager (via the ECS task definition's `secrets` block — see `06_SECRETS.md`):
```
DATABASE_URL
NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD
JWT_SECRET_KEY
TOKEN_ENCRYPTION_KEY
GITHUB_CLIENT_ID, GITHUB_CLIENT_SECRET, GITHUB_WEBHOOK_SECRET   # only if GitHub connect is enabled
```

Never set (no `Settings` field exists for these — they're read directly by `boto3` from the process environment, and in production come from the ECS Task Role instead):
```
AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_SESSION_TOKEN
```

## See also

- `06_SECRETS.md` — which of the above are secrets vs. plain configuration, and where they're stored
- `13_AI_PROVIDER_CONFIGURATION.md` — how the AI-provider fields interact with the stored (installation-wide) and future user-scoped configuration tiers
- `backend/.env.example` — the local-dev-oriented version of this reference (slightly stale versus `config.py`; this document is the authoritative one)
