# 06 — Secrets Management

## Purpose

Every secret GraphForge uses, where it comes from today, where it should come from in production, and how loading/rotation works. Grounded in `backend/app/core/config.py`'s `Settings` class — the single module permitted to read `os.environ` — and `backend/.env.example`.

## Loading mechanism

`Settings` (pydantic-settings, `env_file=".env"` locally) reads every configuration value — including secrets — from environment variables. **No code change is required to introduce Secrets Manager**: ECS's task definition `secrets` field fetches a named secret at task start and injects it as a plain environment variable inside the container, indistinguishable to `Settings` from a value set any other way. This is why `app/core/config.py`'s own docstring calls it *"the only place in the codebase allowed to read environment variables directly"* — that discipline is exactly the seam Secrets Manager slots into.

## Complete secret inventory

| Secret | `Settings` field | Local dev source | Production source | Notes |
|---|---|---|---|---|
| Database credentials | `database_url` | `docker/.env` / `backend/.env` (default: `postgresql+asyncpg://graphforge:graphforge@localhost:5432/graphforge`) | Secrets Manager, e.g. secret `graphforge/prod/database` → `{"url": "postgresql+asyncpg://..."}` | Full connection string, not just a password — includes user, host, db name |
| Neo4j credentials | `neo4j_uri`, `neo4j_user`, `neo4j_password` | `docker/.env` (default password: `graphforge-dev`) | Secrets Manager, e.g. `graphforge/prod/neo4j` | `neo4j_password`'s dev default is one of the three values `Settings._reject_insecure_defaults_in_production` explicitly checks for and refuses to boot with in production |
| JWT signing secret | `jwt_secret_key` | `Settings` default: `dev-only-insecure-secret-change-me` (public, checked into the repo) | Secrets Manager, e.g. `graphforge/prod/auth` → includes this field | **Guarded**: production boot fails loudly if this still equals the dev default |
| Token encryption key | `token_encryption_key` | `Settings` default: a real, valid Fernet key (`7pLY9C3PlWFCWMtvlkhNSMWreEmwwM-oTidOaU-_dmk=`) — public, provides zero real confidentiality | Secrets Manager, same secret as above | **Guarded** the same way. Encrypts GitHub tokens (`app/models/github_connection.py`) and stored AI provider API keys (`app/models/ai_provider_config.py`) at rest via `app/core/crypto.py`. **Rotating this destructively invalidates every already-encrypted value** — see Rotation below |
| GitHub OAuth App credentials | `github_client_id`, `github_client_secret` | Blank by default — "Connect GitHub" returns `503` until set | Secrets Manager, only if this deployment enables GitHub repository connection | Must be **your own** personal/org GitHub OAuth App — never a shared one (`docs/adr/0006-github-integration.md`) |
| GitHub webhook secret | `github_webhook_secret` | Blank by default | Secrets Manager, only if GitHub webhooks are configured | Verifies `POST /webhooks/github` deliveries via HMAC-SHA256; this is a shared secret you also configure on the GitHub webhook itself |
| AWS credentials for Bedrock | *(none — no `Settings` field exists for this)* | `docker/.env`'s `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`/`AWS_SESSION_TOKEN`, read by `boto3` directly from the process environment, never through `Settings` | **No secret to create.** ECS Task Role only (`05_IAM.md`) — this is the one credential category that should never appear in Secrets Manager at all |
| Per-provider API keys (OpenAI/Gemini/Groq/DeepSeek) | `openai_api_key`, `gemini_api_key`, `groq_api_key`, `deepseek_api_key` | Blank by default | Secrets Manager, **only if** this deployment configures a non-Bedrock provider at the environment-fallback tier (`13_AI_PROVIDER_CONFIGURATION.md`) — omit entirely if Bedrock-only | These are also configurable through the UI (installation-wide `AIProviderConfig`, encrypted in Postgres via `app.core.crypto`) as an alternative to the environment tier — see `13_AI_PROVIDER_CONFIGURATION.md` for which tier wins |
| Jira/Confluence credentials | Stored per Knowledge Connection (`app/models/knowledge_connection.py`'s `encrypted_credentials` column), not a `Settings` field | Configured via the Settings UI at runtime | Same — this is application-managed, encrypted-in-Postgres data, not an infrastructure secret Secrets Manager needs to hold | `jira_api_token`/`jira_base_url` `Settings` fields exist as a legacy/env fallback but the primary path today is the Knowledge Connection UI |
| Session/cookie signing secret | **N/A** | — | — | Auth is stateless JWT with no server-side session store or cookie signing (`04_SECURITY.md`) — there is nothing here to provision |
| Encryption keys | Covered above (`token_encryption_key`) | — | — | This is the only application-level encryption key in the codebase |

## Fallback hierarchy

```
1. User-configured secrets (UI)     — AI provider API keys only, once the user-scoped config tier
                                       from 13_AI_PROVIDER_CONFIGURATION.md is implemented; N/A for
                                       infrastructure secrets (DATABASE_URL, JWT_SECRET_KEY, etc.),
                                       which are never user-facing
2. Organization secrets              — today's installation-wide AIProviderConfig table (encrypted via
                                       app.core.crypto with the SAME Fernet key as GitHub tokens);
                                       N/A for infrastructure secrets
3. AWS Secrets Manager               — the production source for every infrastructure secret in the
                                       table above
4. Environment variables (dev only) — docker/.env locally; never the production path for anything
                                       in the table above
```

**Long-lived secrets never live inside the application.** The distinction that matters: Postgres already stores *application-managed* encrypted secrets (GitHub tokens, per-installation AI provider keys) as a real product feature — that's correct and expected. Secrets Manager owns the *infrastructure* secrets that let the app start up and reach its own dependencies in the first place — those never touch the repo, never get baked into a container image, and are never set via ECS's plaintext `environment` field (visible to anyone with `ecs:DescribeTaskDefinition`) — always via the `secrets` field instead, which only exposes the ARN.

## Rotation strategy

| Secret | Rotation approach | Blast radius if delayed |
|---|---|---|
| `DATABASE_URL` / RDS password | Secrets Manager's native RDS rotation Lambda template | Low if delayed — only a security-hygiene concern, not a functional one |
| `NEO4J_PASSWORD` | No built-in AWS rotation template exists for Neo4j — write a small rotation Lambda that changes the Neo4j user's password via the driver and updates the secret, or handle as a manual runbook step initially | Same as above |
| `JWT_SECRET_KEY` | Safe to rotate anytime; invalidates every currently-issued JWT, but the blast radius is naturally small — 60-minute expiry means every session ends within an hour regardless | Every active user is logged out within 60 minutes of rotation — plan for a low-traffic window, but it self-heals fast |
| `TOKEN_ENCRYPTION_KEY` | **Do not rotate casually.** Requires a dedicated migration: decrypt every affected row (GitHub tokens, stored provider API keys) with the old key, re-encrypt with the new key, in the same transaction/deploy window. **No such migration script exists in the codebase today** — this is a required addition before this key can ever be safely rotated (`10_CODE_CHANGES.md`) | High if rotated without the migration — every stored GitHub connection and provider API key becomes silently undecryptable, surfacing as `TokenDecryptionError` (`app/core/crypto.py`) the next time each is used |
| GitHub OAuth secret / webhook secret | Rotate via the GitHub App settings UI + update the Secrets Manager value in the same change | Low — only affects new OAuth flows / webhook signature verification going forward |

## Secrets Manager naming convention (recommended)

```
graphforge/<environment>/database       {"url": "..."}
graphforge/<environment>/neo4j          {"uri": "...", "user": "...", "password": "..."}
graphforge/<environment>/auth           {"jwt_secret_key": "...", "token_encryption_key": "..."}
graphforge/<environment>/github         {"client_id": "...", "client_secret": "...", "webhook_secret": "..."}
graphforge/<environment>/ai-providers   {"openai_api_key": "...", "gemini_api_key": "...", "groq_api_key": "...", "deepseek_api_key": "..."}   (only if used)
```

`<environment>` = `staging` or `production` — this is also how `10_CODE_CHANGES.md` §6.7 (environment separation) recommends parameterizing the CI/CD pipeline per target environment.

## See also

- `05_IAM.md` — which role reads these secrets (`secretsmanager:GetSecretValue`, scoped to these exact ARNs)
- `11_CONFIGURATION.md` — the full `Settings` field reference, including non-secret configuration
- `10_CODE_CHANGES.md` — the missing `TOKEN_ENCRYPTION_KEY` rotation migration, listed as a required change
