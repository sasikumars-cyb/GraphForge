# 04 — Security

## Purpose

What GraphForge's security posture actually is today (verified against the code), what AWS adds around it, and what's recommended before/after production launch. This is an assessment against the real implementation, not a generic security checklist.

## Authentication

- **Mechanism**: local email + password. `POST /api/v1/auth/register`, `POST /api/v1/auth/login` (JSON body — not the OAuth2 form convention Swagger's "Authorize" dialog implies; see `app/api/v1/dependencies.py`'s comment and `docs/adr/0005-authentication.md`), `GET /api/v1/auth/me`.
- **Password storage**: bcrypt (`app/core/security.py`), input truncated to 72 bytes before hashing — bcrypt's own limit, handled defensively so a long password never raises rather than silently weakening the hash.
- **Session model**: stateless JWT (`PyJWT`, HS256, `jwt_secret_key`). No server-side session store, no cookies. The frontend holds the token in `localStorage` (`AuthProvider`/`useAuth`) and sends it as `Authorization: Bearer <token>` on every call.
- **Token expiry**: 60 minutes by default (`access_token_expire_minutes`). No refresh-token mechanism exists — re-login is required after expiry. This is a product/UX characteristic, not a flagged security gap.
- **Scoped tokens**: a JWT can carry a `purpose` claim (e.g. `github_oauth_state`) restricting it to one narrow flow; `get_current_user` explicitly rejects any token with a `purpose` claim as a general bearer token — so a token minted for the GitHub OAuth state parameter can never double as an API session token even if it leaks (referrer header, browser history, logs).
- **GitHub login** (as distinct from GitHub *repository connection*) is implemented (KAN-34) — `app/services/github_login_service.py` finds-or-creates a local `User` by the GitHub profile's verified email and issues the same JWT local login does. Deliberately does not auto-link to an existing `auth_provider="local"` account sharing that email (no account-linking UI exists) — a GitHub sign-in matching a local account's email is rejected with a clear error rather than silently granted access.

## Authorization

- `users.role` column exists (`VARCHAR(32)`, default `'user'`) — added via the lifespan DDL flagged in `10_CODE_CHANGES.md` (fold into a proper Alembic migration as part of that fix, since it currently lives outside the migration chain). Confirm route-level checks against this column are enforced server-side wherever an admin-only action exists, before production launch — this is a **review** item against existing code, not a new feature to build.
- Repository/PR endpoints already enforce an ownership check (the same repository-owner check used across `repositories.py`/`pull_requests.py`, per `docs/architecture/overview.md`'s architecture discovery section, confirmed still present in the current router code) — every workflow/run is similarly scoped to its owning user where a `user_id` column exists (`app/models/workflow.py`, `app/models/run.py` — both nullable for pre-existing rows, non-null for everything created going forward).

## Exception → HTTP status mapping (`app/core/exceptions.py`, `app/core/error_handlers.py`)

| Exception | Status | Notes |
|---|---|---|
| `AppError` (base) | varies (each subclass sets its own) | Logged at `WARNING` |
| `NotFoundError` | 404 | |
| `ConflictError` | 409 | |
| `UnauthorizedError` | 401 | |
| `InvalidTokenError` (subclass of `UnauthorizedError`) | 401 | The frontend's global 401 handler specifically keys off this — "the bearer token/session itself is what's wrong," per its own docstring, which is the signal used to force a logout client-side |
| `ForbiddenError` | 403 | |
| `NotImplementedYetError` | 501 | Used by the GitHub-login stub above |
| `RateLimitedError` | 429 | Defined, but see the Rate Limiting section below — no middleware currently raises this proactively at the edge |
| `RequestValidationError` (FastAPI/Pydantic) | 422 | Logged at `INFO` |
| Any other unhandled `Exception` | 500 | Logged at `ERROR` with full traceback; **the original exception message never reaches the client** — every unhandled error returns a generic message, confirmed by `backend/tests/integration/test_error_handling.py` |

Every error response has the same shape: `{"error": {"code": "...", "message": "..."}}` — consistent across all three handler tiers, and the app is built with `debug=False` specifically so this chain runs rather than Starlette's own HTML debug page ever appearing.

## HTTPS / TLS

- Not handled by the application itself today (local dev is plain HTTP). In AWS: the **ALB terminates TLS** using an ACM certificate (`02_INFRASTRUCTURE.md`), redirecting port 80 → 443 at the listener level — no application code involved.
- ALB → ECS task traffic stays within private subnets, unencrypted at the transport layer by default (standard for this pattern, since the private subnets aren't internet-reachable). Re-encrypting that hop (HTTPS target group) is a defensible hardening step for a stricter compliance posture, not required for a first production deployment.
- **Database connections**: use `sslmode=require` (or stronger) on `DATABASE_URL` against RDS; use `neo4j+s://` (TLS-required scheme) if using Neo4j Aura, which mandates it.

## Encryption

| Data | At rest | In transit |
|---|---|---|
| GitHub access tokens (`GithubConnection.access_token`) | **Already encrypted** via `app/core/crypto.py` (Fernet, `token_encryption_key`) — the only module permitted to import `cryptography` directly | N/A (never transmitted after storage) |
| Stored AI provider API keys (`AIProviderConfig`) | **Already encrypted** the same way, via the same `app.core.crypto` functions (confirmed in `app/ai/config/store.py`'s docstring: "The snapshot holds decrypted keys in memory only. They are never serialized, never logged, and never returned by an API response.") | N/A |
| Password hashes | bcrypt, one-way | N/A |
| Everything else in Postgres | RDS encryption at rest (KMS-backed) — **enable at RDS instance creation**, cannot be added after the fact without a snapshot/restore cycle | `sslmode=require` |
| Neo4j data | EBS encryption if self-hosted; Aura encrypts at rest natively | `neo4j+s://` |
| Secrets Manager entries | Encrypted at rest by default (AWS-managed or customer KMS key) | TLS to the Secrets Manager API |

## Secrets

Full inventory and rotation strategy in `06_SECRETS.md`. Summary: `app/core/config.py`'s `Settings` is the *only* module allowed to read `os.environ`, and it already has a fail-fast guard — `_reject_insecure_defaults_in_production` raises at startup if `ENVIRONMENT=production` and `jwt_secret_key`/`token_encryption_key`/`neo4j_password` still hold their public, checked-in dev defaults. **Do not remove this validator** — it's the mechanism that turns "someone forgot to set a secret in production" into an immediate, loud startup failure instead of a silent, exploitable weakness.

## Security Groups

Full design in `03_NETWORKING.md` — summarized here for completeness: ALB is the only internet-facing surface; backend ECS tasks are reachable only from the ALB's security group; RDS and Neo4j are reachable only from the backend's security group, never from the frontend tier or the internet, even indirectly.

## IAM

Full design in `05_IAM.md` — summarized here: no static AWS access keys anywhere. The app already gets this right for Bedrock (boto3 default credential chain, confirmed in `app/ai/providers/bedrock_provider.py`'s own module docstring: *"GraphForge never stores or handles AWS secret keys directly"*). ECS Task Roles and a GitHub Actions OIDC-federated deploy role extend the same principle to everything else this deployment needs.

## Database access

Enforced at the network layer (security groups, above) rather than relying solely on credential secrecy — even a leaked `DATABASE_URL` password is useless to an attacker without network access to the private data-tier subnet.

## Rate limiting

**Not present in the codebase today** — no rate-limiting middleware exists in `backend/app/`. `RateLimitedError` (429) is defined in `app/core/exceptions.py` but nothing currently raises it proactively. Recommended, not blocking:
- **Edge-level**: AWS WAF on the ALB with a rate-based rule (per-IP request threshold) — zero application code required.
- **Application-level**: a finer-grained per-user/per-endpoint limit, especially on the workflow/agent-run creation endpoints (each one costs real LLM-provider money) — a genuine future improvement, scoped separately in `10_CODE_CHANGES.md`, not a blocker to the first deployment.

## CORS

Non-issue in production under the recommended ALB path-based routing (`03_NETWORKING.md`) — same-origin traffic only, so `Settings.cors_allow_origins` (currently `["http://localhost:5173"]`, a dev-only value) never needs a production domain added. **If** the CloudFront-split-origin alternative is ever adopted instead, this becomes load-bearing again and must include the exact production domain — never `["*"]` in production.

## CSRF

Not applicable in the traditional sense: classic CSRF exploits *ambient* cookie-based auth, and this API uses explicit bearer-token auth (`Authorization` header, sent deliberately by client code, never implicitly attached by the browser the way a cookie is). Confirmed no endpoint relies on an ambient cookie for authentication (Phase 1's review of `auth.py`/`dependencies.py` found only the `OAuth2PasswordBearer` bearer-token path) — this should already be considered closed, not a gap to address.

## Session security

Covered under Authentication above — 60-minute JWT expiry, no refresh token, no server-side revocation list (a logged-out user's still-valid token remains technically usable until it expires — a known, accepted trade-off of pure stateless JWT, not something this deployment blueprint proposes changing).

## Backup strategy

- **PostgreSQL**: RDS automated daily snapshots + point-in-time recovery — both are standard RDS features, enable both, no custom tooling needed.
- **Neo4j**: Aura's built-in backup (if using Aura) or scheduled EBS snapshots (if self-hosted). Either way, **test the actual restore procedure** before you need it for real — see `09_DEPLOYMENT_RUNBOOK.md`.

## Disaster recovery

Multi-AZ RDS gives automatic failover within one region. Cross-region DR is **not recommended for the first production deployment** — nothing in the current product or codebase indicates an RTO/RPO requirement severe enough to justify the added cost and complexity. Revisit if/when a real business continuity requirement emerges.

## Security recommendations summary (priority order)

1. **Enable RDS encryption at rest at creation time** — cannot be retrofitted without a restore cycle; get this right on day one.
2. **Confirm `role`-based authorization checks are actually enforced server-side** for every admin-only action before launch (a code review item, not new code).
3. **Add AWS WAF with a rate-based rule on the ALB** — cheap, zero app-code, closes the current rate-limiting gap at the edge.
4. **Do not remove `Settings._reject_insecure_defaults_in_production`** — it is your production safety net for exactly the secrets this document and `06_SECRETS.md` cover.
5. Application-level rate limiting on AI-provider-calling endpoints — recommended, not blocking (`10_CODE_CHANGES.md`).
6. RDS IAM database authentication instead of a stored password — optional hardening, requires a small code change (`10_CODE_CHANGES.md`), not blocking.

## See also

- `05_IAM.md`, `06_SECRETS.md`, `03_NETWORKING.md` — the three documents this one summarizes and cross-references
- `10_CODE_CHANGES.md` — every gap named above, with priority/complexity/affected-modules detail
