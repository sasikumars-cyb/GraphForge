# GraphForge — AWS Demo Deployment (free tier, single EC2 box)

## Purpose and scope

This is a deliberately simplified deployment track for a **temporary demo**,
not the production architecture in `docs/deployment/01_ARCHITECTURE.md`
through `14_DEPLOYMENT_CHECKLIST.md`. Those documents remain the reference
for a real production deployment (ECS Fargate, ALB, RDS Multi-AZ, Secrets
Manager) — this track exists because most of that architecture's cost
drivers (ALB, NAT Gateway, ECS Fargate, Secrets Manager) have **no AWS free
tier at all**, which doesn't fit a demo.

**What this collapses onto one box**: Postgres, the backend, and the
frontend/Nginx all run as containers on a single free-tier-eligible EC2
instance, fronted by Caddy for automatic HTTPS. Neo4j runs on **Neo4j Aura
Free** instead (a separate managed service, free indefinitely, not just for
12 months) — this keeps the graph database's JVM footprint off a 1GB
instance.

**What this deliberately gives up**, acceptable for a demo, not for real
production: no HA/failover, no auto-scaling, no managed automated backups
(a cron script is provided instead), secrets live in an `.env` file on the
instance's disk instead of Secrets Manager, single point of failure. If this
ever needs to become real production, migrate to the ECS/ALB/RDS design in
the numbered documents instead of hardening this track in place.

**Why the two "blocking" code gaps from `10_CODE_CHANGES.md` don't apply
here**: `/api/v1/health/ready` (§6.1) and separating migrations from boot
(§6.2) both exist to protect a multi-replica ECS rolling deployment behind
an ALB target group. Neither exists in this single-container topology, so
both are safe to skip for this track specifically.

## Cost summary

| Item | Choice | Cost |
|---|---|---|
| EC2 | `t3.micro`/`t4g.micro`, 750 hrs/mo | $0 for 12 months, then ~$7-8/mo |
| EBS | 20-30GB gp3 | $0 (within 30GB free tier) |
| Elastic IP | attached to a running instance | $0 |
| Neo4j | Aura Free | $0, indefinitely |
| Domain/TLS | `sslip.io` + Let's Encrypt (via Caddy) | $0 |
| LLM (Bedrock) | pay-per-token, no idle cost | ~$0.30-1.00 per full workflow run — see cost note below |

## Prerequisites

- AWS account, console access to launch EC2 and (if using Bedrock) enable model access.
- A Neo4j Aura account (free) — create a Free instance at `console.neo4j.io` before starting; copy its connection URI, user, and password.
- This repo cloned somewhere you can `git push`/`git pull` from (GitHub).
- If using Bedrock: request model access in the Bedrock console (Bedrock → Model access) for whichever Claude model you configure — calls fail with access-denied until this is approved, usually near-instant for Anthropic models.
- If you want to eliminate LLM cost entirely instead: a Groq API key (free tier) — set `AI_PROVIDER=groq` and `GROQ_API_KEY` in `docker/.env` instead of the Bedrock fields.

### Before trusting any Bedrock model id: make one real call

`aws bedrock get-foundation-model-availability` reporting `authorizationStatus: AUTHORIZED` is **not sufficient** — verified the hard way in account `186067932947`/`us-east-1`: `config.py`'s own hardcoded default model (`us.anthropic.claude-sonnet-4-20250514`, no version suffix) showed fully authorized/available, but a real call rejected it outright as a deprecated **"Legacy"** model. Two more gotchas the status fields don't surface:
- Newer Claude models on Bedrock must be invoked via their **cross-region inference profile id** (the `us.` prefix, e.g. `us.anthropic.claude-haiku-4-5-20251001-v1:0`), not the bare `anthropic.claude-...` model id — the bare id fails with `"on-demand throughput isn't supported."`
- `bedrock:Converse`/`bedrock:ConverseStream` are **not real IAM actions** (confirmed via `aws accessanalyzer validate-policy` — both come back `INVALID_ACTION`), despite `05_IAM.md` listing them. The Converse/ConverseStream *API operations* are actually authorized by the `bedrock:InvokeModel`/`bedrock:InvokeModelWithResponseStream` IAM actions — [`iam-instance-profile-policy.json`](iam-instance-profile-policy.json) only grants those two.

Before configuring `BEDROCK_MODEL`, verify whatever id you pick with a real, near-zero-cost call:
```bash
aws bedrock-runtime converse --region us-east-1 \
  --model-id us.anthropic.claude-haiku-4-5-20251001-v1:0 \
  --messages '[{"role":"user","content":[{"text":"Reply with exactly one word: OK"}]}]' \
  --inference-config '{"maxTokens":10}'
```
`us.anthropic.claude-haiku-4-5-20251001-v1:0` (cheaper) and `us.anthropic.claude-sonnet-4-5-20250929-v1:0` (higher quality) are both confirmed working as of 2026-07-30 — `docker/.env.ec2-demo.example` defaults to the Haiku one. If you pick a different model, update the `Resource` ARNs in [`iam-instance-profile-policy.json`](iam-instance-profile-policy.json) to match — both the inference-profile ARN and the underlying foundation-model ARNs (a cross-region profile needs permission on both).

## Step 1 — Launch the EC2 instance

1. EC2 console → Launch instance.
2. AMI: **Amazon Linux 2023**.
3. Instance type: **t3.micro** or **t4g.micro** (must show the "Free tier eligible" badge).
4. Key pair: create/download one (needed for SSH).
5. Network settings → Security group: allow
   - `22` (SSH) from **your IP only**, not `0.0.0.0/0`
   - `80` and `443` from `0.0.0.0/0`
6. Storage: 20-30GB gp3 (stays within the 30GB free tier allowance).
7. Advanced details → **User data**: paste the contents of [`ec2-userdata.sh`](ec2-userdata.sh) — installs Docker, the Compose plugin, and a 1GB swap file automatically on first boot.
8. Launch.
9. **Allocate and associate an Elastic IP** to the instance (EC2 console → Elastic IPs) — without this, the public IP changes on every stop/start and breaks the TLS hostname below.

### Attach the Bedrock IAM instance profile (skip if using Groq instead)

1. IAM console → Roles → Create role → **EC2** trusted entity.
2. Attach an inline policy using [`iam-instance-profile-policy.json`](iam-instance-profile-policy.json) — trim the `Resource` list to just the model(s) you'll actually use (same least-privilege rationale as `05_IAM.md`'s Role 2: an unauthorized model swap should fail loudly, not silently work).
3. EC2 console → select the instance → Actions → Security → **Modify IAM role** → attach the role you just created.

## Step 2 — Point a hostname at the Elastic IP

No domain purchase needed: `sslip.io` resolves `<ip-with-dashes>.sslip.io` to that IP automatically, no DNS records to create. For Elastic IP `203.0.113.25`, your hostname is `203-0-113-25.sslip.io`.

(If you'd rather use a real domain, a Route53 hosted zone is ~$0.50/month — create an A record pointing at the Elastic IP instead, and use that domain as `DOMAIN` below.)

## Step 3 — Clone the repo and configure secrets

SSH in (`ssh -i your-key.pem ec2-user@<elastic-ip>`), then:

```bash
git clone <your-repo-url> graphforge && cd graphforge
git checkout branch/ani_cybage   # or whichever branch has the merged changes
cp docker/.env.ec2-demo.example docker/.env
```

Edit `docker/.env`:
- `POSTGRES_PASSWORD` — any strong password.
- `NEO4J_URI`/`NEO4J_USER`/`NEO4J_PASSWORD` — from the Aura console's connection details.
- `JWT_SECRET_KEY` — `openssl rand -base64 48`
- `TOKEN_ENCRYPTION_KEY` — `python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
- `DOMAIN` — the `sslip.io` hostname (or real domain) from Step 2.
- `AI_PROVIDER`/`BEDROCK_REGION`/`BEDROCK_MODEL` — leave as `bedrock` defaults, or switch to `groq` + `GROQ_API_KEY`.

**Never reuse the `JWT_SECRET_KEY`/`TOKEN_ENCRYPTION_KEY` values checked into `backend/.env.example` or `backend/app/core/config.py` — those are public.**

## Step 4 — Bring the stack up

```bash
docker compose -f docker/docker-compose.ec2-demo.yml up --build -d
docker compose -f docker/docker-compose.ec2-demo.yml logs -f caddy   # watch for the ACME/Let's Encrypt cert being issued
```

Alembic migrations run automatically on backend startup (existing `backend/docker-entrypoint.sh`) — no separate migration step needed on a single box.

## Step 5 — Smoke test

```bash
curl https://<your-domain>/api/v1/health
```
Then in a browser: `https://<your-domain>` should load the SPA with a valid padlock. Register a test account, log in, and run one lightweight Planning workflow to confirm Bedrock (or Groq) connectivity end-to-end — this is the one check that actually exercises the IAM instance profile / API key.

## Redeploying after a change

```bash
./scripts/deploy-ec2-demo.sh
```
Pulls the latest commit and rebuilds/restarts the stack in place.

## Backups (no RDS, so this isn't automatic)

Install the cron job:
```bash
crontab -e
# add: 0 3 * * * /home/ec2-user/graphforge/docs/deployment/demo/backup-postgres.sh
```
Dumps Postgres nightly to `~/graphforge-backups/`, keeping 7 days. Neo4j Aura Free has its own built-in backup — nothing to do there.

## Cost guardrail (do this before making the demo public)

`docs/deployment/04_SECURITY.md` confirms **no rate limiting exists anywhere in the app today**. On a public demo with an LLM-calling backend, an undiscovered-but-guessable registration endpoint is a real cost exposure, not just a security one. Before sharing the URL widely:

1. AWS Billing console → Budgets → create a budget (e.g. $10/month) with an email alert.
2. Consider not publicizing the exact URL, and deleting/rotating test accounts after the demo, until rate limiting (`10_CODE_CHANGES.md` §6.10) exists.

## Tearing it down

`docker compose -f docker/docker-compose.ec2-demo.yml down` stops the stack; terminate the EC2 instance and release the Elastic IP via the console to stop all charges (the Aura Free instance can be left running indefinitely at $0, or deleted from the Aura console).
