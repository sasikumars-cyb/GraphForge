# 14 — Deployment Checklists

## Purpose

Pass/fail checklists for each stage of getting GraphForge into production. Each item cross-references the document with the full detail — this file is the quick-scan version, not a replacement for reading them.

## Infrastructure Ready

- [ ] `networking` module applied — VPC, public/private subnets across 2 AZs, IGW, NAT Gateway(s) (`03_NETWORKING.md`)
- [ ] Security groups created exactly as scoped in `03_NETWORKING.md` (no `0.0.0.0/0` inbound anywhere except `sg-alb`'s 80/443)
- [ ] `iam` module applied — all 4–5 roles from `05_IAM.md` exist, trust policies correct
- [ ] RDS PostgreSQL provisioned, Multi-AZ enabled, **encryption at rest enabled at creation** (`04_SECURITY.md` — cannot be added later without a restore cycle)
- [ ] Neo4j provisioned (Aura or self-hosted EC2 — decision recorded, per `02_INFRASTRUCTURE.md`)
- [ ] ALB provisioned with the two path-based listener rules (`/api/*` → backend, `/*` → frontend — `03_NETWORKING.md`)
- [ ] Route53 hosted zone + ACM certificate issued and attached to the ALB's HTTPS listener
- [ ] ECS cluster created
- [ ] ECR repositories created (`graphforge-backend`, `graphforge-frontend`)

## Secrets Ready

- [ ] Every secret in `06_SECRETS.md`'s inventory table exists in Secrets Manager, named per its recommended convention
- [ ] `JWT_SECRET_KEY` and `TOKEN_ENCRYPTION_KEY` generated fresh — **never reused from any value that ever appeared in `backend/app/core/config.py`, `backend/.env.example`, or this repository's git history** (those are public by definition)
- [ ] `DATABASE_URL` populated with the real RDS endpoint (only possible after RDS provisioning above)
- [ ] `NEO4J_URI`/`NEO4J_USER`/`NEO4J_PASSWORD` populated with real values
- [ ] GitHub OAuth secrets populated **only if** GitHub repository connection is enabled for this deployment
- [ ] `05_IAM.md` Role 1 (`graphforge-ecs-execution-role`) has `secretsmanager:GetSecretValue` scoped to exactly these secret ARNs

## Database Ready

- [ ] `alembic upgrade head` run successfully against the production database (one-off ECS task — `07_CICD.md`, `09_DEPLOYMENT_RUNBOOK.md`)
- [ ] `alembic current` on production matches `alembic heads` in the deployed commit's `backend/alembic/versions/`
- [ ] Neo4j connectivity confirmed with a trivial `RETURN 1` query using the credentials stored in Secrets Manager

## CI/CD Ready

- [ ] `.github/workflows/ci.yml`'s existing lint/test/build jobs pass on the commit being deployed
- [ ] The CD extension (`07_CICD.md`) is wired: Docker build → ECR push → task definition registration → migration task → service update → health verification, in that order
- [ ] `graphforge-github-actions-deploy-role` (`05_IAM.md`, Role 4) trust policy correctly scoped to this repository and branch via OIDC — **no AWS access key stored as a GitHub secret**
- [ ] ECS service has `deploymentCircuitBreaker: { enable: true, rollback: true }` configured (automatic rollback mechanism)
- [ ] Images are tagged by git SHA in the task definition — never `:latest`

## Application Ready

- [ ] `GET /api/v1/health/ready` implemented and deployed (`10_CODE_CHANGES.md` §6.1) — **blocking**, ALB target group health checks depend on it
- [ ] ALB target group health check points at `/health/ready`, not `/health`
- [ ] `ENVIRONMENT=production` and `DEBUG=false` set in the task definition
- [ ] Backend ECS service `desiredCount` set to **1** (`02_INFRASTRUCTURE.md`'s constraint — do not raise until `10_CODE_CHANGES.md` §6.3 is addressed)
- [ ] `AI_PROVIDER` set (recommend `bedrock` per `10_CODE_CHANGES.md` §6.4), and the Task Role's Bedrock IAM permissions (`05_IAM.md`, Role 2) match the configured `bedrock_model` exactly

## Production Ready

- [ ] Backend task boots successfully with `ENVIRONMENT=production` — if it fails with `Refusing to start with ENVIRONMENT=production while these settings still hold their insecure default value`, that's a secrets-wiring problem to fix, not a bug to bypass
- [ ] All smoke tests pass (`09_DEPLOYMENT_RUNBOOK.md`'s table: readiness endpoint, login, `/auth/me`, a real end-to-end workflow run reaching `completed`, frontend shell renders)
- [ ] HTTPS works end-to-end from the real production domain
- [ ] CloudWatch dashboards and alarms provisioned (`12_OPERATIONS.md`)
- [ ] AWS WAF rate-based rule attached to the ALB (`10_CODE_CHANGES.md` §6.10 — recommended, not strictly blocking, but cheap and should be in place before real traffic)

## Post-Deployment

- [ ] CloudWatch Logs retention explicitly set (never left at indefinite) for both log groups
- [ ] Backup/restore procedure tested at least once for both Postgres and Neo4j (`12_OPERATIONS.md` — an untested backup is not a backup)
- [ ] Confirm no `recovered_orphaned_runs` log lines appear under normal operation (a deploy will legitimately produce one if a run was in flight — track the *rate*, not zero-tolerance, per `10_CODE_CHANGES.md` §6.3)
- [ ] Confirm `role`-based authorization checks are enforced server-side for every admin-only action (`04_SECURITY.md` — a review item against existing code)

## Rollback Ready

- [ ] Previous ECS task definition revision confirmed still registered and valid (this is automatic as long as SHA-tagging discipline is followed — verify it hasn't been manually deregistered)
- [ ] Rollback command tested at least once in staging: `aws ecs update-service --task-definition <previous-revision>`
- [ ] Database migrations for the current release confirmed backward-compatible with the previous application version (expand/contract pattern — `09_DEPLOYMENT_RUNBOOK.md`), so an application rollback never requires an accompanying schema rollback
- [ ] On-call knows the incident table in `09_DEPLOYMENT_RUNBOOK.md` — particularly that an orphaned-run failure after a deploy is a known, documented limitation, not a page-worthy new bug

## See also

- `09_DEPLOYMENT_RUNBOOK.md` — the narrative version of these same steps
- `10_CODE_CHANGES.md` — full detail on every "blocking" item referenced above
