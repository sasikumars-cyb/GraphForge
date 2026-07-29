# 10 — Required & Recommended Code Changes

## Purpose

Every code change identified while designing this deployment, ordered by how much each one blocks a safe production launch — not by file order. **Nothing in this document is implemented.** Each entry states the reason, priority, estimated complexity, affected modules, dependencies, and whether it blocks production.

## How to read the priority/complexity columns

- **Priority**: `Blocking` (must be done before or as part of the first production deployment), `Blocking-for-scale` (fine to defer if you accept a stated constraint, but blocks removing that constraint), `Recommended` (should be done, not required), `Optional` (a genuine enhancement, no urgency).
- **Complexity**: `Small` (hours), `Medium` (a few days, one focused PR), `Large` (a design review plus multi-PR effort).

---

## 6.1 — Readiness health check

| | |
|---|---|
| **Priority** | Blocking |
| **Complexity** | Small |
| **Affected modules** | `backend/app/api/v1/routers/health.py`, `backend/app/schemas/health.py` |
| **Dependencies** | None |

**Reason**: `GET /api/v1/health` is liveness-only by explicit design — its own docstring says *"Deliberately has no dependency on the database or any external service"* and *"A separate `/health/ready` (checking DB connectivity) can be added once there's a database to check."* There now is one (two, in fact — Postgres and Neo4j). ALB target group health checks and ECS-level readiness need to know the app can actually reach both, not just that the process is running — otherwise a task with a severed DB connection stays "healthy" and keeps receiving traffic.

**Proposed implementation**: add `GET /api/v1/health/ready`, attempting a lightweight query against Postgres (`SELECT 1` via the existing session dependency) and Neo4j (a trivial `RETURN 1` via `app/graph/session.py`'s `get_driver()`), each with a short timeout (~2s), returning `503` if either fails. Keep `/health` exactly as-is for the ECS **container** healthcheck (fast, no dependencies — already referenced by `backend/Dockerfile`'s `HEALTHCHECK` instruction); point the **ALB target group** health check at `/health/ready` instead.

**Migration impact**: None — purely additive endpoint.
**Rollout**: Ship in the same release as the ECS task definition/ALB target group configuration that first references it.

---

## 6.2 — Separate schema migration from application boot

| | |
|---|---|
| **Priority** | Blocking |
| **Complexity** | Medium |
| **Affected modules** | `backend/app/main.py` (the `lifespan` function), `backend/alembic/versions/` |
| **Dependencies** | None to start; §6.1 and the CI/CD pipeline (`07_CICD.md`) benefit from landing alongside |

**Reason**: `app/main.py`'s `lifespan` hook runs raw, idempotent DDL (`ALTER TABLE users ADD COLUMN IF NOT EXISTS role ...`, `CREATE TABLE IF NOT EXISTS knowledge_connections ...`) **on every process start**, in addition to the real Alembic migration chain. At one replica this is inelegant but harmless. At N replicas during a rolling ECS deploy, multiple tasks run overlapping DDL concurrently against the same database at startup — `IF NOT EXISTS` prevents a destructive error, but this is still schema mutation happening as a side effect of a scaling/deploy event rather than a controlled, single, pre-deploy step.

**Proposed implementation**: a dedicated one-off ECS task (already specified in `07_CICD.md`'s pipeline) runs `alembic upgrade head` against production **before** the new application task definition is deployed. Before removing the lifespan DDL block, explicitly diff it against `backend/alembic/versions/`'s migration history to confirm no schema drift — do this diff, don't assume it's already covered.

**Migration impact**: Requires the drift diff above before the DDL block can be safely removed.
**Rollout**: Land the "Alembic-as-pipeline-step" change first, verify a full deploy cycle runs it correctly, **then** remove the lifespan DDL block in a follow-up release. Do not do both in the same deploy.

---

## 6.3 — Durable background execution

| | |
|---|---|
| **Priority** | Blocking-for-scale (backend can launch at `desiredCount=1` without this; blocks any horizontal scaling of the backend and blocks lossless deploys) |
| **Complexity** | Large — design review required before implementation |
| **Affected modules** | `backend/app/orchestrator/background_execution.py`, `backend/app/orchestrator/run_coordinator.py`, `backend/app/main.py` (the `recover_orphaned_runs` call site changes meaning once a real queue exists), `backend/app/indexer/workers/index_worker.py` (identical pattern, identical fix, per its own code comment referencing the same trade-off) |
| **Dependencies** | None technically, but this is the single largest change in this document — treat it as its own project, not a bullet point to knock out alongside the others |

**Reason**: agent-run execution runs via `asyncio.create_task()` **on the same event loop as the web server** — not Celery, not SQS, not any durable queue. The module's own docstring: *"this does not survive a process restart or scale across multiple worker processes."* `recover_orphaned_runs()` (called from `main.py`'s lifespan on every boot) finds any `Run` left `"running"`/`"queued"` from a previous process and marks it `"failed"` — it does not resume it.

**Proposed implementation** (a direction, not a prescription — the implementing engineer should make this call with current team context): the standard AWS-native option is **Amazon SQS**, consumed either by the same backend containers (an SQS poller alongside `uvicorn` in the same task) or — the better long-term shape — a **separate ECS service dedicated to run execution**, scaled independently of the web tier, which would only ever create a queued `Run` row and return `202`.

**Migration impact**: Touches how a Run's entire lifecycle is orchestrated, not just where it executes. Needs its own focused design review — this document identifies *that* it's needed and *why*, not the full detailed design of the replacement.
**Rollout**: Can be deferred past the *first* production deployment if the team explicitly accepts the `desiredCount=1` constraint (`02_INFRASTRUCTURE.md`). Make that trade-off visible to stakeholders rather than silently accepting it — track how often the "orphaned run" runbook entry (`09_DEPLOYMENT_RUNBOOK.md`) actually fires as the evidence for when to prioritize this.

---

## 6.4 — AI provider resolution: user-scoped configuration tier

| | |
|---|---|
| **Priority** | Recommended |
| **Complexity** | Medium |
| **Affected modules** | `backend/app/ai/config/store.py`, `backend/app/ai/config/resolver.py`, `backend/app/models/ai_provider_config.py` (or a new model file for a user-scoped table), a new Alembic migration, `backend/app/api/v1/routers/ai_workspace.py` |
| **Dependencies** | None |

**Reason**: full detail in `13_AI_PROVIDER_CONFIGURATION.md`. The requested User → Organization → Bedrock hierarchy doesn't fully exist today — the "stored" config layer (`AIProviderConfig`/`AISettings`) is installation-wide, not per-user, and there is no `Organization` model anywhere in the codebase (confirmed by search — "Organization name" in the Settings UI is a display-only field, not a tenancy boundary).

**Proposed implementation**: a new `user_ai_provider_config` table (same shape as today's `ProviderRecord`, scoped by `user_id`, encrypted the same way via `app.core.crypto`), checked by the resolver **before** the existing installation-wide stored default. Change `Settings.ai_provider`'s default from `"openai"` to `"bedrock"` so an installation with nothing configured anywhere automatically and correctly falls back to Bedrock, which needs no stored secret at all.

**Migration impact**: Additive table — no existing data affected. The one-line default change affects only installations that have configured *nothing* (new deployments) — anything with an existing installation-wide or environment-level OpenAI/Gemini/Groq configuration is unaffected, since that configuration still wins over the new lower-priority default.
**Rollout**: Independent of everything else in this document — ship whenever convenient.

---

## 6.5 — Structured (JSON) logging

| | |
|---|---|
| **Priority** | Recommended |
| **Complexity** | Small |
| **Affected modules** | `backend/app/core/logging.py` |
| **Dependencies** | None |

**Reason**: `app/core/logging.py` uses plain stdlib logging today — its own comment: *"Kept deliberately simple (stdlib `logging`, not structlog) until real request [volume justifies more]."* CloudWatch Logs Insights queries are far more useful against structured (queryable-field) log lines than free text. Not required — CloudWatch ingests plain text fine — but worth doing before leaning on Logs Insights for incident response (`12_OPERATIONS.md`).

**Migration impact**: Log format only, no behavioral change.
**Rollout**: Anytime, no coordination needed with anything else in this document.

---

## 6.6 — Configuration validation for AWS-specific requirements

| | |
|---|---|
| **Priority** | Optional |
| **Complexity** | Small |
| **Affected modules** | `backend/app/core/config.py` |
| **Dependencies** | Whichever specific hardening change (e.g. §6.8 below) introduces a new production-only requirement |

**Reason**: `Settings._reject_insecure_defaults_in_production` is a strong existing pattern — fail fast at boot, not at first request. Extend the same philosophy to new production-only requirements this deployment introduces, the same way the existing validator already does for JWT/Fernet/Neo4j secrets.

**Migration impact**: None.
**Rollout**: Bundle with whichever specific hardening change introduces the new requirement — not a standalone change.

---

## 6.7 — Environment separation (deployment configuration, not application code)

| | |
|---|---|
| **Priority** | Blocking |
| **Complexity** | Small (it's a pipeline/infra parameterization task, not really a code change) |
| **Affected modules** | None in `backend/app/` — every relevant value is already a `Settings` field, correctly sourced from the environment |
| **Dependencies** | `06_SECRETS.md`'s naming convention, `07_CICD.md`'s pipeline |

**Reason**: `Settings.environment` already gates the insecure-defaults check. For clean staging/production separation, every environment-specific value (`cors_allow_origins`, `frontend_base_url`, `github_oauth_redirect_uri`) needs to be supplied per-environment via the task definition — this is already possible with zero code change, since all of them are already `Settings` fields reading from the environment. **The actual work is confirming the CI/CD pipeline parameterizes the task definition per target environment** (separate Secrets Manager paths, per `06_SECRETS.md`), not touching `config.py`.

**Migration impact**: None.
**Rollout**: Verify as part of standing up the staging environment, before production.

---

## 6.8 — Optional hardening: RDS IAM database authentication

| | |
|---|---|
| **Priority** | Optional |
| **Complexity** | Medium |
| **Affected modules** | `backend/app/database/session.py` (the engine's connection needs a token-generating callable instead of a static password in the connection string) |
| **Dependencies** | `05_IAM.md`'s note on this — the Task Role would need `rds-db:connect` added, scoped to the specific DB instance/user |

**Reason**: removes `DATABASE_URL`'s password from Secrets Manager entirely, replaced by short-lived (15-minute) IAM-generated auth tokens. A genuine security improvement, not required for a first production deployment.

**Migration impact**: Requires generating and testing the token-refresh logic in the SQLAlchemy engine setup — not a drop-in config change.
**Rollout**: After the initial production deployment is stable; independent of everything else.

---

## 6.9 — `TOKEN_ENCRYPTION_KEY` rotation migration

| | |
|---|---|
| **Priority** | Recommended (becomes Blocking the moment key rotation is actually needed — e.g. suspected compromise) |
| **Complexity** | Medium |
| **Affected modules** | A new one-off script (no existing module owns this) that reads every row with a Fernet-encrypted column (`github_connections.access_token`, `ai_provider_configs`' encrypted key columns), decrypts with the old `TOKEN_ENCRYPTION_KEY`, re-encrypts with the new one, in a single transaction/deploy window |
| **Dependencies** | `06_SECRETS.md`'s rotation section |

**Reason**: no such migration script exists in the codebase today. Rotating `TOKEN_ENCRYPTION_KEY` without it silently breaks every stored GitHub connection and provider API key — each becomes undecryptable, surfacing as `TokenDecryptionError` (`app/core/crypto.py`) the next time it's used, not at rotation time itself.

**Migration impact**: This *is* the migration — write it before you ever need to rotate this key under pressure.
**Rollout**: Build and test this against a staging copy of production-shaped data before the key is ever rotated for real.

---

## 6.10 — Rate limiting

| | |
|---|---|
| **Priority** | Recommended |
| **Complexity** | Small (edge/WAF) / Medium (application-level) |
| **Affected modules** | None for the WAF option (pure infrastructure); a new FastAPI dependency/middleware for the application-level option |
| **Dependencies** | None |

**Reason**: no rate-limiting exists today (`RateLimitedError` is defined in `app/core/exceptions.py` but nothing raises it proactively). Every workflow/agent-run creation call costs real LLM-provider money — worth limiting per-user/per-endpoint eventually, but AWS WAF at the ALB (`04_SECURITY.md`) closes the most urgent gap (abuse/DoS) with zero application code.

**Migration impact**: None for WAF; the application-level version would need to decide what "too many requests" means per endpoint, which is a product decision as much as a technical one.
**Rollout**: WAF rule can ship independently, anytime. Application-level limiting is a separate, later piece of work.

---

## Summary — blocking vs. deferrable

| Blocking for first production deployment | Blocking only for horizontal scaling | Recommended | Optional |
|---|---|---|---|
| §6.1 Readiness endpoint | §6.3 Durable background execution | §6.4 AI provider user tier | §6.8 RDS IAM auth |
| §6.2 Separate migrations from boot | | §6.5 Structured logging | §6.6 Config validation extensions |
| §6.7 Environment separation (pipeline config) | | §6.9 Token-encryption-key rotation migration | |
| | | §6.10 Rate limiting | |

## See also

- `01_ARCHITECTURE.md` — where each affected module sits in the overall system
- `02_INFRASTRUCTURE.md` — the `desiredCount=1` constraint §6.3 drives
- `07_CICD.md` — where §6.1/§6.2 plug into the deployment pipeline
- `13_AI_PROVIDER_CONFIGURATION.md` — full design for §6.4
