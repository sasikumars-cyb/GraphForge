# 09 — AWS Defense

Grounded entirely in `docs/deployment/*.md` — an existing, code-verified
deployment blueprint, not aspirational slideware. State clearly if asked:
this describes the **designed/specified** production deployment; the
hackathon demo runs on local Docker Compose, not this AWS stack.

## Architecture

ALB → two ECS Fargate services (backend, frontend) → RDS PostgreSQL
(Multi-AZ) + Neo4j (Aura or self-hosted EC2) → Bedrock via IAM Task Role.
ECR for images, Secrets Manager for secrets, CloudWatch for logs/metrics,
Route53 + ACM for DNS/TLS, CloudTrail for account-level audit.

## Networking / VPC

Path-based ALB routing: `/api/*` → backend, `/*` → frontend — a direct
AWS-native replacement for the existing `docker-compose.prod.yml` Nginx
proxy rule, not a new design. Backend/RDS/Neo4j live in private subnets,
reachable only from their respective upstream security group — never the
internet, never each other's unrelated tier.

## IAM — the one-sentence summary

**Zero static AWS access keys, anywhere, for anything GraphForge does
itself.** Bedrock resolves credentials through boto3's default chain via
the ECS Task Role (confirmed directly in `bedrock_provider.py`'s own
docstring: "GraphForge never stores or handles AWS secret keys
directly"). Five roles total, each scoped to exactly what it needs:

| Role | Scope |
|---|---|
| ECS Task Execution Role | ECR pull (2 named repos), CloudWatch Logs write (2 named log groups), Secrets Manager read (named ARNs only) |
| Backend Task Role | `bedrock:InvokeModel`/`Converse` on exactly the configured model ARNs — one line per model, never a wildcard |
| Frontend Task Role | Nothing beyond ECS's structural requirement |
| GitHub Actions Deploy Role | OIDC-federated (no stored key), ECR push + ECS register/update/run-task, `iam:PassRole` on exactly the three roles above |
| (optional) Rotation Lambda Role | Named secret + underlying credential-change access only |

**A Bedrock model swap failing with access-denied is the intended
behavior**, not a bug — the policy is scoped to literal configured model
ARNs by design (least privilege), and a new model requires a deliberate
IAM update.

## Security

- TLS terminated at the ALB (ACM cert); DB connections use
  `sslmode=require`/`neo4j+s://`.
- Encryption at rest: GitHub tokens and stored AI provider keys already
  encrypted via Fernet (`app.core.crypto`) before they ever reach
  Postgres; RDS encryption at rest — **must be enabled at creation**,
  cannot be retrofitted without a restore cycle.
- `Settings._reject_insecure_defaults_in_production` — fails startup
  loudly if `jwt_secret_key`/`token_encryption_key`/`neo4j_password`
  still hold their public, checked-in dev defaults. "Do not remove this
  validator" — it's the production safety net.
- CORS is a non-issue under same-origin ALB routing; CSRF doesn't apply
  (explicit bearer-token auth, never ambient cookie auth).
- **Rate limiting is not present in the codebase today** — recommended,
  not blocking: AWS WAF rate-based rule at the edge (zero app code), plus
  a future application-level limit on AI-provider-calling endpoints
  specifically (each one costs real money per call).

## Secrets Manager

`Settings` is the only module permitted to read `os.environ` — this
discipline is exactly the seam Secrets Manager slots into via ECS's
`secrets` task-definition field (fetched at task start, injected as a
plain env var, zero code change). Full inventory: database credentials,
Neo4j credentials, JWT secret, token encryption key, GitHub OAuth
credentials (if enabled), per-provider API keys (if non-Bedrock is
configured). **AWS credentials for Bedrock are the one category that
should never appear in Secrets Manager at all** — Task Role only.

**Rotation caution worth knowing cold**: `TOKEN_ENCRYPTION_KEY` cannot be
rotated casually — it requires a dedicated re-encryption migration that
doesn't exist in the codebase today. Rotating it without that migration
makes every stored GitHub connection and provider API key silently
undecryptable. State this as a known, documented pre-requisite, not a
surprise gap.

## CloudWatch / Monitoring

- Logs: `awslogs` driver, zero code change, one log group per service,
  explicit retention set (never left indefinite).
- Metrics: ECS Container Insights (CPU/memory/task-count) out of the box;
  custom metrics (workflow-stage completion/failure rate, AI-provider
  latency/error rate) are a small, scoped follow-on, not yet emitting.
- **The single most interesting alarm to describe**: a CloudWatch Logs
  metric filter on the literal string `"recovered_orphaned_runs"` —
  because that log line is the visible symptom of the background-
  execution durability gap (in-process `asyncio.Task` doesn't survive a
  restart). "Alarming on it turns a silent data-loss event into a paged,
  trackable incident, and gives you the frequency data needed to
  prioritize fixing it." This is a genuinely good answer to "how do you
  monitor for known limitations" — lead with it if asked.
- Tracing (X-Ray/OTel): not present today, deliberately deferred until
  the durable-queue redesign — premature before that, since a request's
  causal chain is currently one process/one call stack.

## Scaling

- Frontend: stateless, scales horizontally without constraint.
- **Backend: fixed at `desiredCount=1`** until the durable-execution
  redesign ships. State the honest reason: scaling past 1 today doesn't
  corrupt data (every replica correctly serves whatever the ALB routes to
  it, reading true state from Postgres) but defeats the purpose of
  horizontal scaling, since a run started on replica A is invisible to
  replica B's in-memory task set.
- RDS: vertical scaling is the near-term lever; no read-replica need
  identified yet.
- Neo4j: Aura's own tiers, or vertical EC2 scaling if self-hosted — no
  horizontal/clustering story in the current setup.

## High availability / Disaster recovery

Multi-AZ RDS gives automatic failover within a region — this **is** the
current DR posture. Cross-region DR is explicitly **not recommended** for
this deployment — "nothing in the current codebase or product
requirements suggests an RTO/RPO that demands it." This is a considered,
stated decision, not an oversight — say so if pressed on "what about a
whole-region outage."

## Cost optimization

Not the subject of a dedicated ADR in this codebase — answer honestly if
asked for specific numbers ("not measured/modeled yet"). The architecture
choices that are cost-relevant and defensible: Fargate over EC2 (no idle
instance cost for 2 lightweight services), `desiredCount=1` (not
over-provisioned), S3 confirmed unused by the application itself (no
speculative storage cost), no ElastiCache/Redis (no session store to
back, no existing cache layer to migrate) — every "not built" here is
also a "not paying for it yet."

## Deployment pipeline

CI (`.github/workflows/ci.yml`) is real: lint/test/build on every push/PR
to `master`. **The CD extension is a specification, not yet
implemented** — say this precisely, don't imply automated AWS deploys
exist today. When built: Docker build → ECR push → task definition
registration → one-off Alembic migration task → service update → smoke
tests (readiness endpoint, login, `/auth/me`, a real end-to-end
Planning-workflow run reaching `completed` — the one check that exercises
real Bedrock connectivity and IAM permissions end-to-end).

## Expected AWS interview questions with answers

**Q: Why Fargate over EC2/EKS/App Runner/Lambda?**
A: Evaluated explicitly against this codebase — EC2 only wins with
GPU/bin-packing/AMI needs (none apply); EKS's complexity isn't earned by
2 services; App Runner lacks VPC-native private DB access and fine IAM
granularity; Lambda's execution-time limits fight long-running,
stateful-mid-flight LLM agent calls.

**Q: How do you handle secrets rotation?**
A: Per-secret strategy, not one blanket policy — RDS password via
Secrets Manager's native rotation Lambda template; JWT secret is safe to
rotate anytime (60-minute token expiry self-heals); the token encryption
key is the hard one and explicitly requires a migration that doesn't
exist yet — stated as a known pre-requisite, not hidden.

**Q: What's your blast radius if the ECS Task Execution Role is
compromised?**
A: Scoped to exactly 2 ECR repos, 2 log groups, and named Secrets Manager
ARNs — cannot read any other secret or pull any other image in the
account. This is the explicit least-privilege rationale documented for
this role.

**Q: How would you add a new Bedrock model?**
A: Update `bedrock_model` config AND the Task Role's IAM policy in the
same change — a mismatch fails closed (access-denied), by design, not a
bug to route around.

**Q: What happens on a bad deploy?**
A: ECS deployment circuit breaker auto-rolls-back if new tasks never
reach healthy; manual rollback is `aws ecs update-service --task-definition
<previous-revision>`, safe because every image is SHA-tagged, never
`:latest`.
