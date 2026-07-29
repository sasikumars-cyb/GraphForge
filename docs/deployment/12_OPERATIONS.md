# 12 — Operations

## Purpose

Monitoring, logging, alerting, scaling, incident response, and backup/DR for the production deployment — what to watch, what to alarm on, and how to respond.

## Logging

- **Today**: `backend/app/core/logging.py` — plain stdlib `logging`, deliberately simple per its own comment (*"until real request [volume justifies more]"*). Every unhandled exception is logged at `ERROR` with a full traceback by the 3-tier exception handler chain (`app/core/error_handlers.py`); `AppError` subclasses log at `WARNING`; validation errors at `INFO`.
- **In AWS**: the `awslogs` log driver on both ECS services captures container stdout/stderr into CloudWatch Logs with **zero code change** — one log group per service (`/ecs/graphforge-backend`, `/ecs/graphforge-frontend`). Set an explicit retention period (e.g. 30 days) — never leave it at "never expire" by accident.
- **Recommended improvement**: structured (JSON) logging (`10_CODE_CHANGES.md` §6.5) makes CloudWatch Logs Insights queries dramatically more useful (queryable fields instead of free-text grep). Not required to launch.

## Metrics

- **ECS Container Insights**: CPU/memory/task-count per service, enabled at the cluster level, zero code change.
- **Custom application metrics** (recommended, small effort once §6.5 lands): workflow-stage completion/failure rate (`app/orchestrator/run_coordinator.py` already transitions through exactly these states — emit a metric or structured log line at each transition), AI-provider call volume/latency/error rate by vendor (every provider's `LLMResponse`, per `app/ai/providers/base.py`, already returns latency and token counts — just needs to be emitted), count of workflow runs by stage/status.

## Tracing

**Not present today** — no OpenTelemetry/X-Ray instrumentation exists in the codebase. Recommended to add **AWS X-Ray** (or OpenTelemetry exporting to it) once the durable-queue work (`10_CODE_CHANGES.md` §6.3) lands — that's exactly the point at which a request's causal chain stops being "one process, one call stack" and starts genuinely needing distributed tracing to follow a run across the web tier and a worker tier. Premature before that; not blocking.

## Health checks

| Check | Target | Purpose |
|---|---|---|
| ECS container-level `HEALTHCHECK` | `GET /api/v1/health` (already defined in `backend/Dockerfile`; frontend's `HEALTHCHECK` hits `/` via `wget`) | "Is this task's process alive" — no dependency checks, by design |
| ALB target group health check | `GET /api/v1/health/ready` (new — `10_CODE_CHANGES.md` §6.1) | "Should this task receive traffic" — checks real Postgres/Neo4j connectivity |

## Alerts (CloudWatch Alarms)

| Alarm | Condition | Why |
|---|---|---|
| ALB 5xx rate | Above a defined threshold over N minutes | User-facing errors |
| ALB target group unhealthy host count | `> 0` | A task is failing its readiness check |
| ECS running-task-count vs. desired-count | Sustained mismatch | A task is crash-looping or failing to start |
| RDS CPU / free storage / connection count | Standard thresholds | Database resource exhaustion |
| Neo4j instance status (if self-hosted EC2) | Instance status check failure | Same category, for the un-managed option |
| CloudWatch Logs metric filter on `"recovered_orphaned_runs"` | Any occurrence | This log line (`app/orchestrator/background_execution.py`) is the visible symptom of the background-execution durability gap (`10_CODE_CHANGES.md` §6.3) — alarming on it turns a silent data-loss event into a paged, trackable incident, and gives you the frequency data needed to prioritize fixing it |
| CloudWatch Logs metric filter on `ERROR`-level lines | Above a defined rate | Catches unhandled exceptions the 3-tier handler chain already logs |

## Dashboard

One CloudWatch dashboard per environment (staging, production): ALB request count/latency/error rate; ECS CPU/memory per service; RDS connections/CPU/storage; workflow-stage completion and failure rate; AI-provider call volume and error rate by vendor (once the custom metrics above are emitting).

## Scaling

- **Frontend service**: stateless, scales horizontally without constraint — Nginx serving static files has no shared-state concern.
- **Backend service**: **fixed at `desiredCount=1`** until `10_CODE_CHANGES.md` §6.3 (durable background execution) is implemented — see `02_INFRASTRUCTURE.md` for the full rationale. Scaling this past 1 today does not corrupt data (every replica correctly serves whichever HTTP request the ALB routes to it, reading true state from Postgres), but it defeats the purpose of horizontal scaling and makes deploys lossier, since a run started on replica A is invisible to replica B's in-memory task set.
- **RDS**: vertical scaling (instance class) is the near-term lever; read replicas are not currently justified by anything in the codebase (no read-heavy pattern identified that a single primary can't serve).
- **Neo4j**: Aura's own scaling tiers, or vertical scaling of the self-hosted EC2 instance — no horizontal/clustering story in the current Community-Edition-equivalent setup.

## Incident response

See `09_DEPLOYMENT_RUNBOOK.md`'s incident table for the concrete symptom → cause → response mapping (ECS boot failures from missing secrets, ALB unhealthy targets, orphaned-run failures after deploys, Bedrock access-denied errors, `TOKEN_ENCRYPTION_KEY` rotation breakage). This document covers the *monitoring* that surfaces those symptoms; the runbook covers the *response*.

## Backup

- **PostgreSQL**: RDS automated daily snapshots + point-in-time recovery — both standard RDS features, enable both at creation.
- **Neo4j**: Aura's built-in backup, or scheduled EBS snapshots if self-hosted.

## Restore

Test the actual restore procedure — for both Postgres and Neo4j — before you need it for real. This isn't optional diligence; an untested backup is not a backup. Document the tested restore steps as part of standing up each environment, and re-verify after any major version upgrade of either database.

## Disaster recovery

Multi-AZ RDS provides automatic failover within a region — this is the DR posture for the first production deployment. Cross-region DR is explicitly **not recommended** for this deployment given the current codebase and product maturity — nothing in `01_ARCHITECTURE.md`'s review surfaces an RTO/RPO requirement severe enough to justify the added cost and operational complexity. Revisit if a real business-continuity requirement emerges.

## Operational best practices

- Never leave CloudWatch Logs retention unset (defaults to indefinite, which is a cost and compliance liability, not a safety net).
- Treat the `_reject_insecure_defaults_in_production` startup failure as a *working* signal, not a bug — see `09_DEPLOYMENT_RUNBOOK.md`'s incident table.
- Track the frequency of orphaned-run recoveries as the concrete evidence for when to prioritize `10_CODE_CHANGES.md` §6.3 — don't let it become background noise.
- Any Terraform `apply` against production infrastructure should go through the same reviewed pipeline discipline as an application deploy (`08_TERRAFORM_STRUCTURE.md`), not ad-hoc local runs.

## See also

- `09_DEPLOYMENT_RUNBOOK.md` — incident response detail
- `10_CODE_CHANGES.md` — the durability/monitoring gaps this document's alarms are designed to surface
- `05_IAM.md` — the permissions CloudWatch/X-Ray access requires
