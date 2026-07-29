# 09 — Deployment Runbook

## Purpose

Step-by-step instructions to take GraphForge from nothing to a running production deployment on AWS, and to operate it afterward. Written so a new engineer (or another AI) can execute this without re-deriving the design decisions documented in `01`–`08`.

## Prerequisites

- AWS account with sufficient service quotas for VPC/ECS/RDS/NAT Gateway — check default quotas before starting, don't discover a limit mid-provisioning.
- A registered domain, or a subdomain delegated to a Route53 hosted zone.
- Terraform installed (`08_TERRAFORM_STRUCTURE.md`) for whoever runs the initial `apply`.
- GitHub repository configured with an OIDC trust relationship to the deploy IAM role (`05_IAM.md`, Role 4) — no long-lived AWS credentials stored as GitHub Actions secrets.
- **Decision made before starting**: Neo4j Aura vs. self-hosted EC2 (`02_INFRASTRUCTURE.md`) — this determines which `infra/modules/neo4j/` implementation you write.
- Local familiarity with the existing dev workflow is genuinely useful context: `scripts/docker-dev.sh` runs the local stack this deployment is based on; `scripts/test.sh`/`scripts/lint.sh` run the exact same checks `.github/workflows/ci.yml` runs.

## AWS resource provisioning order

Mirrors `08_TERRAFORM_STRUCTURE.md`'s module dependency graph:

1. **`networking`** module — VPC, subnets, IGW, NAT, security groups (`03_NETWORKING.md`). Everything else depends on this.
2. **`iam`** module — every role in `05_IAM.md`. Needed before ECS or the CI/CD pipeline can do anything.
3. **`secrets-manager`** module — create the secret *entries* (placeholder values are fine at this stage).
4. **Populate real secret values**: generate `JWT_SECRET_KEY`/`TOKEN_ENCRYPTION_KEY` now (e.g. `openssl rand -base64 48` for the JWT secret; `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` for the Fernet key — **never reuse the values checked into `backend/app/core/config.py` or `backend/.env.example`, they are public**). `DATABASE_URL` can only be populated with a real value after step 5.
5. **`rds-postgres`** module, then **`neo4j`** module (Aura: provision via the Aura console/API and store its connection details in Secrets Manager; self-hosted: EC2 + EBS module).
6. **`alb` + `route53-acm`** modules — start ACM's DNS validation early (it can take several minutes), in parallel with step 5.
7. **`ecs-cluster`** module.
8. **`ecs-service`** module, once per service (backend, frontend). **Do not let the backend service's desired count go above 0 until step 9 (migrations) has run against a real, empty schema** — sequence the very first deploy so migrations run first, or accept that the first backend task will crash-loop against an unmigrated database.
9. **Run the initial Alembic migration** (`alembic upgrade head`) via a one-off `aws ecs run-task`, against the now-provisioned, empty RDS database — the same command `.github/workflows/ci.yml` already runs against its ephemeral test database (`07_CICD.md`).
10. **`monitoring`** module — dashboards and alarms (`12_OPERATIONS.md`); can be provisioned any time after the resources they monitor exist.

## Environment variables and secrets

Populate in Secrets Manager **before** the first backend task starts (full detail: `06_SECRETS.md`): `DATABASE_URL`, `NEO4J_URI`/`NEO4J_USER`/`NEO4J_PASSWORD`, `JWT_SECRET_KEY`, `TOKEN_ENCRYPTION_KEY`, and — only if this deployment enables GitHub repository connection — `GITHUB_CLIENT_ID`/`GITHUB_CLIENT_SECRET`/`GITHUB_WEBHOOK_SECRET`.

Set `ENVIRONMENT=production` as a plain (non-secret) task definition environment variable. This activates `Settings._reject_insecure_defaults_in_production` (`backend/app/core/config.py`) — if any required secret above is missing or still holds its dev default, **the task fails to start with an explicit error naming exactly which field is wrong**. Treat that failure as the system working correctly, not as a bug to route around.

## Database initialization

Covered in provisioning step 9 above. After it runs, confirm `alembic current` (run against the production database, e.g. via the same one-off task mechanism with the command overridden) reports the same revision as `alembic heads` in `backend/alembic/versions/` in the deployed commit — this confirms the migration actually applied cleanly before proceeding.

## Neo4j initialization

No schema-migration system exists for Neo4j in this codebase (it's schema-less by nature; the indexer creates nodes/relationships as it discovers architecture — `app/indexer/graph/builder.py`). Nothing to initialize beyond the instance existing and being reachable with the credentials stored in Secrets Manager. Confirm connectivity with a trivial `RETURN 1` Cypher query (the same check the readiness endpoint from `10_CODE_CHANGES.md` §6.1 performs) before the first backend deploy.

## First deployment

1. Confirm provisioning steps 1–10 above are complete and the database is migrated.
2. Set the backend ECS service's `desiredCount` explicitly to **1** (`02_INFRASTRUCTURE.md`'s constraint, driven by the background-execution durability gap documented in `10_CODE_CHANGES.md` §6.3) — do not raise this until that gap is addressed.
3. Deploy via the CI/CD pipeline (`07_CICD.md`), not manually — even for the first deployment, since this is exactly the run that most needs the pipeline itself validated.
4. Watch `aws ecs wait services-stable`, then hit `/api/v1/health/ready` (`10_CODE_CHANGES.md` §6.1) directly through the ALB's generated DNS name — before Route53/ACM necessarily finish propagating — to confirm the app is actually up before moving to DNS cutover.
5. Point Route53 at the ALB; confirm the ACM certificate is issued and attached; confirm HTTPS works end-to-end from the real domain.

## Smoke tests (run after every deploy — automate as the pipeline's final gate, per `07_CICD.md`)

| Check | Expected result |
|---|---|
| `GET /api/v1/health/ready` | `200` |
| `POST /api/v1/auth/login` with a known-good test account | Valid JWT returned |
| `GET /api/v1/auth/me` with that JWT | Expected user returned |
| Trigger a lightweight Planning workflow against a trivial objective | Reaches `completed` — this is the one check that exercises real Bedrock connectivity and the Task Role's IAM permissions (`05_IAM.md`) end-to-end, which nothing earlier in the pipeline touches |
| Load the root URL in a browser | SPA shell renders — catches a broken Nginx config or bad frontend build immediately |

## Rollback

- **Automatic**: the ECS deployment circuit breaker (`07_CICD.md`) rolls back to the previous task definition revision if new tasks never reach healthy.
- **Manual**: `aws ecs update-service --task-definition <previous-revision>` for failures only visible under real traffic. This is why every image is tagged by git SHA, never `:latest` (`07_CICD.md`).
- **Database migrations do not automatically roll back.** Write migrations to be backward-compatible with the *previous* application version wherever possible (expand/contract pattern: add nullable columns/tables in one release, backfill/dual-write in a second, remove the old shape only in a third) — this is a convention for whoever writes future migrations, not something Alembic enforces automatically.

## Upgrade process

Standard: merge to `master` → `.github/workflows/ci.yml`'s existing lint/test/build gates run → the CD extension (`07_CICD.md`) builds, migrates, deploys, and smoke-tests. For any schema change, prefer expand/contract over a single destructive migration, given the rollback constraint above.

## Operational runbook — common incidents

| Symptom | Likely cause | First response |
|---|---|---|
| ECS task fails to start; logs show `ValueError: Refusing to start with ENVIRONMENT=production while these settings still hold their insecure default value: ...` | A required secret wasn't populated in Secrets Manager, or the task definition's `secrets` block references the wrong ARN | Check the named field(s) in the error message against Secrets Manager (`06_SECRETS.md`) — this error is the app working correctly |
| ALB target group shows unhealthy targets | `/health/ready` failing — Postgres or Neo4j unreachable | Check security groups first (`03_NETWORKING.md`) — the most common cause after any networking change — then check RDS/Neo4j instance status directly |
| A workflow run is stuck `"queued"`/`"running"` after a deploy, then flips to `"failed"` right after | Expected, given the background-execution gap in `10_CODE_CHANGES.md` §6.3 — the backend process handling that run was replaced mid-execution | User re-runs the workflow. This is the known, documented limitation, not a new bug — track frequency as evidence for prioritizing §6.3 |
| CloudWatch Logs show repeated `recovered_orphaned_runs` on every deploy | Same root cause as above (`app/orchestrator/background_execution.py`'s `recover_orphaned_runs`) | Same — this log line is the visible symptom the durable-queue work would eliminate |
| Bedrock calls fail with access-denied | Task Role's Bedrock permissions (`05_IAM.md`, Role 2) don't include the specific model ID currently configured (`bedrock_model`), or the model isn't enabled for this account/region in the Bedrock console | Compare the effective `bedrock_model` value against the Task Role's `Resource` ARNs — a model change requires a matching IAM policy update, by design (least privilege, not an oversight) |
| Secrets Manager rotation breaks GitHub connections / stored AI provider keys | `TOKEN_ENCRYPTION_KEY` was rotated without the re-encryption migration | See `06_SECRETS.md`'s rotation section — this key requires a dedicated migration before rotation, which doesn't exist yet (`10_CODE_CHANGES.md`) |

## See also

- `02_INFRASTRUCTURE.md` through `06_SECRETS.md` — the design each provisioning step implements
- `07_CICD.md` — the pipeline referenced throughout this runbook
- `14_DEPLOYMENT_CHECKLIST.md` — the same steps as pass/fail checklists
- `10_CODE_CHANGES.md` — every gap referenced in the incident table, with full detail
