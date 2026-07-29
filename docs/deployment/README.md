# GraphForge Production Deployment Documentation

This directory is the single source of truth for deploying GraphForge to AWS. It exists so another engineer — or another AI — can deploy GraphForge to production **without reverse-engineering the codebase again.**

Every document here is grounded directly in the repository as it exists today (specific files, line-level behavior, actual test/lint commands, the real CI workflow) — not generic cloud-deployment advice with GraphForge's name attached. Where something here differs from other documentation elsewhere in `docs/` (some of which describes an earlier phase of the product), **this directory follows the implementation**. If you find a discrepancy, trust the code, then fix the discrepancy here.

## Recommended reading order

**If you're deploying for the first time**, read in this order:

1. **[01_ARCHITECTURE.md](01_ARCHITECTURE.md)** — how GraphForge actually works: frontend, backend, database, Neo4j, AI providers, the workflow engine, auth, background execution, integrations. Read this first regardless of your role — everything else assumes it.
2. **[02_INFRASTRUCTURE.md](02_INFRASTRUCTURE.md)** — which AWS services this deployment uses and why, mapped onto the architecture from step 1.
3. **[03_NETWORKING.md](03_NETWORKING.md)** — VPC, subnets, security groups, routing — the concrete network design.
4. **[10_CODE_CHANGES.md](10_CODE_CHANGES.md)** — read this **before** provisioning anything. It lists what's blocking (must happen before/during the first deployment) versus recommended versus optional, including one hard constraint (`desiredCount=1` for the backend) that shapes the infrastructure design in steps 2–3.
5. **[05_IAM.md](05_IAM.md)** and **[06_SECRETS.md](06_SECRETS.md)** — every role and every secret, concretely.
6. **[11_CONFIGURATION.md](11_CONFIGURATION.md)** — the full environment variable reference.
7. **[08_TERRAFORM_STRUCTURE.md](08_TERRAFORM_STRUCTURE.md)** — how to structure the infrastructure-as-code that implements steps 2–6.
8. **[07_CICD.md](07_CICD.md)** — the deployment pipeline, extending the CI workflow that already exists (`.github/workflows/ci.yml`).
9. **[09_DEPLOYMENT_RUNBOOK.md](09_DEPLOYMENT_RUNBOOK.md)** — step-by-step execution, in order, referencing everything above.
10. **[14_DEPLOYMENT_CHECKLIST.md](14_DEPLOYMENT_CHECKLIST.md)** — the same steps as pass/fail checklists, for the actual deployment day.

**If you're doing a narrower task**, jump directly to the relevant document:

| I need to... | Read |
|---|---|
| Understand a specific subsystem (auth, workflow engine, AI providers) | `01_ARCHITECTURE.md` |
| Design or review the security posture | `04_SECURITY.md` |
| Add or audit an IAM permission | `05_IAM.md` |
| Add, rotate, or troubleshoot a secret | `06_SECRETS.md` |
| Change or extend the CI/CD pipeline | `07_CICD.md` |
| Write the actual Terraform | `08_TERRAFORM_STRUCTURE.md` (structure/rationale only — no `.tf` code exists yet) |
| Diagnose a production incident | `09_DEPLOYMENT_RUNBOOK.md`'s incident table, then `12_OPERATIONS.md` |
| Decide what to build next / understand a known limitation | `10_CODE_CHANGES.md` |
| Look up an environment variable | `11_CONFIGURATION.md` |
| Set up monitoring/alarms | `12_OPERATIONS.md` |
| Understand or extend AI provider selection | `13_AI_PROVIDER_CONFIGURATION.md` |
| Run through a deployment checklist | `14_DEPLOYMENT_CHECKLIST.md` |

## Document index

| File | Contents |
|---|---|
| [01_ARCHITECTURE.md](01_ARCHITECTURE.md) | System architecture: frontend, backend, database, Neo4j, AI providers, workflow engine, auth, background execution, external integrations, component interaction diagrams |
| [02_INFRASTRUCTURE.md](02_INFRASTRUCTURE.md) | AWS services used and why, compute-platform decision rationale (ECS Fargate vs. alternatives), frontend-hosting options |
| [03_NETWORKING.md](03_NETWORKING.md) | VPC layout, subnets, security groups, route tables, port mappings, traffic flow, ALB routing |
| [04_SECURITY.md](04_SECURITY.md) | Authentication, authorization, HTTPS/TLS, encryption, rate limiting, CORS/CSRF, session security, backup/DR |
| [05_IAM.md](05_IAM.md) | Every IAM role — purpose, permissions, resources, trust policy, least-privilege rationale |
| [06_SECRETS.md](06_SECRETS.md) | Every secret — source, usage, rotation strategy, Secrets Manager naming, loading sequence |
| [07_CICD.md](07_CICD.md) | The existing CI pipeline (`.github/workflows/ci.yml`) plus the specified CD extension — build, migrate, deploy, verify, rollback |
| [08_TERRAFORM_STRUCTURE.md](08_TERRAFORM_STRUCTURE.md) | IaC tool recommendation (Terraform) and repository structure — documentation only, no code |
| [09_DEPLOYMENT_RUNBOOK.md](09_DEPLOYMENT_RUNBOOK.md) | Step-by-step deployment instructions, smoke tests, rollback, upgrade process, incident response |
| [10_CODE_CHANGES.md](10_CODE_CHANGES.md) | Every required/recommended/optional code change, with priority, complexity, affected modules, and whether it blocks production |
| [11_CONFIGURATION.md](11_CONFIGURATION.md) | Every configuration value/environment variable, defaults, required-vs-optional, dev-vs-prod |
| [12_OPERATIONS.md](12_OPERATIONS.md) | Monitoring, logging, metrics, alerts, scaling, incident response, backup/restore, DR |
| [13_AI_PROVIDER_CONFIGURATION.md](13_AI_PROVIDER_CONFIGURATION.md) | Current provider resolution architecture, and the design to evolve it into User → Organization → Bedrock |
| [14_DEPLOYMENT_CHECKLIST.md](14_DEPLOYMENT_CHECKLIST.md) | Pass/fail checklists: infrastructure, secrets, database, CI/CD, application, production, post-deployment, rollback readiness |

## The one thing to internalize before anything else

GraphForge's background agent-run execution is **in-process, not a durable queue** (`asyncio.create_task`, tied to the specific backend process that accepted the request — see `01_ARCHITECTURE.md` and `10_CODE_CHANGES.md` §6.3). It does not survive a process restart and does not coordinate across replicas. This is not a defect introduced by this documentation effort; it's an accurate description of the current implementation, verified by reading `backend/app/orchestrator/background_execution.py`'s own docstring. It is the single fact that most shapes the infrastructure design in this directory (the backend service's `desiredCount=1` constraint) — read `10_CODE_CHANGES.md` §6.3 before deciding to deviate from that constraint.

## Keeping this documentation accurate

This directory reflects the repository at the time it was written. If the implementation changes — a new AI provider, a schema migration, a change to the auth model — **update the relevant document in the same change**, the same discipline the codebase's own `docs/adr/` already follows. Documentation that silently drifts from the implementation is worse than no documentation, because it's actively misleading rather than obviously absent.
