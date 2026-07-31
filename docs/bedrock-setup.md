# Amazon Bedrock Provider Setup

GraphForge supports Amazon Bedrock as a first-class AI provider alongside OpenAI and Google Gemini. Bedrock gives you access to Claude (Anthropic), Amazon Nova, and Meta Llama models through your AWS account.

## Supported Providers

| Provider | Auth Method | Key Required in GraphForge |
|----------|------------|---------------------------|
| OpenAI | API Key | Yes |
| Google Gemini | API Key | Yes |
| **Amazon Bedrock** | AWS credential chain | **No** |

## Supported Models

| Model | ID | Context Window |
|-------|-----|----------------|
| Claude Sonnet 4 | `us.anthropic.claude-sonnet-4-20250514` | 200K |
| Claude Opus 4 | `us.anthropic.claude-opus-4-20250514` | 200K |
| Claude Haiku 3.5 | `us.anthropic.claude-haiku-3-5-20250620` | 200K |
| Amazon Nova Pro | `us.amazon.nova-pro-v1:0` | 300K |
| Amazon Nova Lite | `us.amazon.nova-lite-v1:0` | 300K |
| Amazon Nova Micro | `us.amazon.nova-micro-v1:0` | 128K |
| Meta Llama 4 Maverick | `us.meta.llama4-maverick-17b-instruct-v1:0` | 128K |
| Meta Llama 4 Scout | `us.meta.llama4-scout-17b-instruct-v1:0` | 128K |

Additional models can be added to the registry without code changes.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    GraphForge Agents                         │
│   (Planning, Development, Review, Testing — all agnostic)   │
└───────────────────────────┬─────────────────────────────────┘
                            │  ILLMProvider.complete()
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                  Provider Factory + Resolver                  │
│   resolve(provider="bedrock", stage="planning")             │
└───────┬───────────────┬───────────────┬─────────────────────┘
        │               │               │
        ▼               ▼               ▼
┌─────────────┐ ┌─────────────┐ ┌─────────────────┐
│   OpenAI    │ │   Gemini    │ │ Amazon Bedrock   │
│  Provider   │ │  Provider   │ │   Provider       │
│  (httpx)    │ │  (httpx)    │ │   (boto3)        │
└─────────────┘ └─────────────┘ └────────┬────────┘
                                          │
                                          ▼
                                ┌──────────────────┐
                                │  AWS Credential  │
                                │     Chain        │
                                │                  │
                                │ • Env vars       │
                                │ • ~/.aws/creds   │
                                │ • IAM Role       │
                                │ • EC2/ECS meta   │
                                └──────────────────┘
```

## Required IAM Permissions

The IAM principal running GraphForge needs these permissions:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "BedrockInvoke",
      "Effect": "Allow",
      "Action": [
        "bedrock:InvokeModel",
        "bedrock:InvokeModelWithResponseStream"
      ],
      "Resource": [
        "arn:aws:bedrock:*::foundation-model/anthropic.*",
        "arn:aws:bedrock:*::foundation-model/amazon.*",
        "arn:aws:bedrock:*::foundation-model/meta.*"
      ]
    },
    {
      "Sid": "BedrockModelAccess",
      "Effect": "Allow",
      "Action": [
        "bedrock:ListFoundationModels",
        "bedrock:GetFoundationModel"
      ],
      "Resource": "*"
    }
  ]
}
```

To restrict to specific models, narrow the Resource ARNs:

```json
"Resource": [
  "arn:aws:bedrock:us-east-1::foundation-model/anthropic.claude-sonnet-4-20250514-v1:0"
]
```

## Required AWS Services

- **Amazon Bedrock** — model invocation
- **IAM** — authentication and authorization
- **AWS STS** (if using assumed roles or temporary credentials)

Ensure the models you want to use are enabled in your Bedrock console (Model Access page).

## Configuration

### Option 1: Environment Variables (simplest for development)

```bash
# .env or shell environment
AI_PROVIDER=bedrock
BEDROCK_REGION=us-east-1
BEDROCK_MODEL=us.anthropic.claude-sonnet-4-20250514

# AWS credentials (standard SDK env vars)
AWS_ACCESS_KEY_ID=AKIA...
AWS_SECRET_ACCESS_KEY=...
AWS_SESSION_TOKEN=...        # optional, for temporary credentials
AWS_PROFILE=my-profile       # alternative to explicit keys
AWS_DEFAULT_REGION=us-east-1 # fallback if BEDROCK_REGION not set
```

### Option 2: Settings UI (recommended for production)

1. Navigate to **Settings > AI Providers**
2. Find **Amazon Bedrock** in the provider list
3. Set the **AWS Region** (e.g., `us-east-1`)
4. Select a **Model** from the dropdown
5. Click **Test connection** to verify credentials
6. Click **Save**

### Option 3: AWS CLI Profile

```bash
# Configure a profile
aws configure --profile graphforge

# Set the profile for GraphForge
export AWS_PROFILE=graphforge
```

### Option 4: IAM Role (EC2/ECS/EKS)

When running on AWS infrastructure, attach an IAM role to your compute resource. No credential configuration is needed — the SDK discovers credentials automatically from the instance metadata service.

## Credential Chain (priority order)

The AWS SDK resolves credentials in this order:

1. **Environment variables** — `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`
2. **Shared credentials file** — `~/.aws/credentials` (profile from `AWS_PROFILE`)
3. **AWS config file** — `~/.aws/config`
4. **ECS container credentials** — task role
5. **EC2 instance metadata** — instance profile role
6. **SSO credentials** — `aws sso login`

GraphForge never stores, reads, or transmits AWS secret keys. All credential handling is delegated entirely to the AWS SDK.

## Local Development Setup

```bash
# 1. Install dependencies
cd backend
pip install -e ".[dev]"

# 2. Configure AWS credentials (pick one method)
aws configure                    # Interactive setup
# OR
export AWS_PROFILE=my-profile    # Use existing profile
# OR
export AWS_ACCESS_KEY_ID=...     # Direct credentials
export AWS_SECRET_ACCESS_KEY=...

# 3. Set GraphForge to use Bedrock
export AI_PROVIDER=bedrock
export BEDROCK_REGION=us-east-1

# 4. Verify access
aws bedrock list-foundation-models --region us-east-1 --query "modelSummaries[?contains(modelId, 'claude')]"

# 5. Run the backend
uvicorn app.main:app --reload
```

## Docker Compose (local dev)

`docker/docker-compose.yml` wires AWS auth into the `backend` container two ways at once, so whichever method you use "just works" with no code changes:

1. **AWS SSO via profile (recommended)** — the container mounts your host's `~/.aws` directory read-only at `/root/.aws` (the dev image's `dev` stage has no `USER` directive, so it runs as root — see `backend/Dockerfile`). Set `AWS_PROFILE` in `docker/.env` and the container sees the same profile config and cached SSO token your host CLI does.
2. **Explicit temporary credentials** — set `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_SESSION_TOKEN` in `docker/.env`, same as before. Still fully supported for developers who prefer it.

Copy `docker/.env.example` to `docker/.env` to get both documented.

### One-time setup, then never copy credentials again

```bash
# Host machine, once:
aws configure sso
aws sso login --profile AdministratorAccess-186067932947

# docker/.env:
AWS_PROFILE=AdministratorAccess-186067932947

# Then every day:
docker compose -f docker/docker-compose.yml up --build
```

When the SSO session expires (`ExpiredTokenException` in backend logs), re-run `aws sso login --profile ...` **on the host** — no container rebuild or restart needed, since `~/.aws` is a live bind mount and boto3 re-reads the cached token on the next request.

### Precedence — don't set both

boto3's credential chain checks environment variables *before* the shared profile. If both `AWS_ACCESS_KEY_ID` and `AWS_PROFILE` are set in `docker/.env`, the explicit keys win silently and `AWS_PROFILE` is ignored. Pick one method per `docker/.env`.

(An `AWS_ACCESS_KEY_ID` line left blank does *not* count as "set" — botocore's env provider treats an empty string as absent and falls through to the next provider, which is why `docker-compose.yml` can safely default all of these to empty without breaking the profile-based path.

`AWS_PROFILE` is the one exception: botocore treats `AWS_PROFILE=""` as an explicit, invalid profile name and raises `ProfileNotFound` rather than falling through - a crash, not a graceful fallback. Two layers guard against this:
1. `docker-compose.yml` uses Compose's bare-name pass-through (`- AWS_PROFILE`, no `=value`) for this one variable instead of the usual `${VAR:-}` default, so Compose omits the key from the container **entirely** when `docker/.env` doesn't define it - not "sets it to empty", genuinely absent. This is why `docker/.env.example` ships `AWS_PROFILE` commented out rather than blank.
2. `backend/docker-entrypoint.sh` additionally unsets `AWS_PROFILE` at container start if it's still empty - a backstop for the cases layer 1 can't reach: a literal blank `AWS_PROFILE=` line in `docker/.env` (e.g. uncommented without a value), a stray `export AWS_PROFILE=` left in a shell profile, or the same image run outside Compose entirely (`docker run`, an ECS/EKS task definition templated with an empty value). Compose-level configuration can't distinguish "meant to be blank" from "not set" once a value reaches the container - only the entrypoint can.)

### Migrating off pasted temporary credentials

If your `docker/.env` currently has `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`/`AWS_SESSION_TOKEN` pasted from the AWS Console:

1. Run `aws configure sso` once (or reuse an existing profile from `~/.aws/config`).
2. Run `aws sso login --profile <name>`.
3. In `docker/.env`, delete the three `AWS_*` key lines (or leave them blank) and set `AWS_PROFILE=<name>` instead.
4. `docker compose -f docker/docker-compose.yml up --build` — no further credential copying, ever, as long as you re-run `aws sso login` when a session expires.

### CI/CD and production

No Docker Compose changes are needed for either:

- **CI/CD** — GitHub Actions/Jenkins/GitLab CI inject `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`/`AWS_SESSION_TOKEN` (or use OIDC to assume a role, e.g. `aws-actions/configure-aws-credentials`) into the process environment before `docker compose` runs; the existing passthrough in `docker-compose.yml` picks them up automatically.
- **Production** (`docker-compose.prod.yml`, ECS/EC2/EKS/Lambda) — no `AWS_*` variables are set at all. The IAM role attached to the compute resource (ECS task role, EC2 instance profile, EKS IRSA) is discovered automatically via instance/container metadata. See `docs/deployment/05_IAM.md` for the exact IAM role/policy GraphForge needs in production.

### If the `~/.aws` mount causes problems

If you don't use the AWS CLI at all, Docker will silently create an empty `~/.aws` directory on your host the first time you run `docker compose up` (standard Docker behavior for a bind mount whose source doesn't exist yet). This is harmless — delete it any time — and doesn't affect any other credential method.

## Troubleshooting

### "AccessDeniedException"

- The IAM principal lacks `bedrock:InvokeModel` permission.
- The model may not be enabled in your Bedrock console. Go to **Amazon Bedrock > Model access** and request access.

### "ResourceNotFoundException" / "Could not resolve the foundation model"

- The model ID is incorrect or the model is not available in your region.
- Check available models: `aws bedrock list-foundation-models --region us-east-1`
- Cross-region inference model IDs are prefixed with `us.` (e.g., `us.anthropic.claude-sonnet-4-20250514`).

### "ExpiredTokenException"

- Temporary credentials (STS) have expired. Refresh with `aws sso login` or rotate your session token.

### "ThrottlingException"

- You've exceeded Bedrock's invocation rate. The fallback system (if enabled) will retry on another provider. Otherwise, wait and retry.
- Request a quota increase in the AWS console under **Service Quotas > Amazon Bedrock**.

### "EndpointConnectionError"

- The configured region is incorrect or the Bedrock service endpoint is unreachable.
- Verify the region: `aws bedrock list-foundation-models --region <your-region>`

### Provider shows "Not tested" in Settings

- Click **Test connection** to run a live validation.
- Ensure credentials are available to the process running GraphForge.

## AI Profiles with Bedrock

Create profiles that use Bedrock for specific workflow stages:

1. Go to **Settings > AI Providers > Profiles**
2. Create a profile (e.g., "Fast Planner") with:
   - Provider: Amazon Bedrock
   - Model: `us.anthropic.claude-sonnet-4-20250514`
   - Temperature: 0.1
3. Map it to a stage (e.g., Planning)

All agents (Planning, Development, Review, Testing) work identically regardless of which provider backs their profile.

## Future Enhancements

- **Streaming support** — The Bedrock Converse API supports streaming via `ConverseStream`; the provider architecture is ready for this.
- **Dynamic model discovery** — Query `ListFoundationModels` to show all enabled models in the UI.
- **Cross-region inference** — Automatically route to the lowest-latency region.
- **Guardrails integration** — Apply Bedrock Guardrails for content filtering.
- **Knowledge Bases** — Integrate Bedrock Knowledge Bases for RAG-enhanced agents.
