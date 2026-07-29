# 05 — IAM Strategy

## Purpose

Every IAM role this deployment requires, with purpose, permissions, resources, trust policy, and least-privilege rationale. Principle: **no static AWS access keys, anywhere, for anything GraphForge does itself.**

The application already earns this for Bedrock — `app/ai/providers/bedrock_provider.py`'s module docstring states plainly: *"Credentials are resolved through the standard AWS credential chain: environment variables, `~/.aws/credentials`, IAM roles, EC2/ECS instance profiles. GraphForge never stores or handles AWS secret keys directly."* Confirmed by reading the provider's `_get_client()` method — it calls `boto3.client("bedrock-runtime", ...)` with no explicit credentials, letting the SDK's default chain resolve them. Every role below extends that same principle to the rest of the deployment.

---

## Role 1 — ECS Task Execution Role

**Name**: `graphforge-ecs-execution-role`

**Purpose**: Used by the ECS agent (not application code) to prepare a task for launch — pull the container image, write logs, and fetch secrets to inject as environment variables.

**Trust policy**: `ecs-tasks.amazonaws.com`

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": { "Service": "ecs-tasks.amazonaws.com" },
    "Action": "sts:AssumeRole"
  }]
}
```

**Permissions**:

| Action | Resource | Why |
|---|---|---|
| `ecr:GetAuthorizationToken` | `*` (required by the API — this action has no resource-level restriction in ECR) | Needed once per pull to authenticate to ECR |
| `ecr:BatchGetImage`, `ecr:GetDownloadUrlForLayer` | `arn:aws:ecr:<region>:<account>:repository/graphforge-backend`, `.../graphforge-frontend` | Scoped to exactly the two repositories this app builds |
| `logs:CreateLogStream`, `logs:PutLogEvents` | `arn:aws:logs:<region>:<account>:log-group:/ecs/graphforge-backend:*`, `.../graphforge-frontend:*` | Scoped to exactly the two log groups (`12_OPERATIONS.md`) |
| `secretsmanager:GetSecretValue` | The exact ARNs listed in `06_SECRETS.md` — never `secretsmanager:*` on `*` | This is what makes the ECS task definition's `secrets` block (as opposed to plaintext `environment`) work |

**Least-privilege rationale**: this role never touches application logic — it exists purely so ECS itself can stage a task. Scoping it to two ECR repos, two log groups, and a named list of secret ARNs means a compromise of this role (e.g. via a misconfigured task definition) cannot read any *other* secret in the account, nor pull any *other* image.

---

## Role 2 — ECS Task Role (Backend)

**Name**: `graphforge-backend-task-role`

**Purpose**: What the *running FastAPI process* can do at runtime.

**Trust policy**: `ecs-tasks.amazonaws.com` (same shape as above)

**Permissions**:

| Action | Resource | Why |
|---|---|---|
| `bedrock:InvokeModel`, `bedrock:InvokeModelWithResponseStream`, `bedrock:Converse`, `bedrock:ConverseStream` | `arn:aws:bedrock:<region>::foundation-model/us.anthropic.claude-sonnet-4-*`, `.../us.anthropic.claude-haiku-4-5-*` — **one line per model actually configured** via `bedrock_model` (`app/core/config.py`), never a wildcard across every Bedrock model | This is the entire steady-state permission set the application code needs — `app/ai/providers/bedrock_provider.py`'s `_send_completion`/`complete_with_tools` methods call exactly these two Converse API operations, nothing else |

**Explicitly not granted, and why**:
- No S3 permissions — confirmed nothing in the codebase writes to S3 (`02_INFRASTRUCTURE.md`).
- No RDS IAM auth permission (`rds-db:connect`) **unless** the optional IAM-database-auth hardening (`10_CODE_CHANGES.md`) is implemented — until then, RDS access is credential-based (Secrets Manager password), not IAM-based, so this permission would be unused and should not be granted speculatively.
- No permission to modify its own IAM role, no `iam:*` of any kind — the running application never needs to touch IAM.

**Least-privilege rationale**: scoped to the literal two Bedrock API operations the code calls, against the literal model ARNs configured for this deployment. A Bedrock model swap via the AI provider config UI (`13_AI_PROVIDER_CONFIGURATION.md`) that isn't pre-authorized here will fail with an IAM access-denied error — **this is the intended behavior**, not a bug to route around; update the policy deliberately when adding a new model.

---

## Role 3 — ECS Task Role (Frontend)

**Name**: `graphforge-frontend-task-role`

**Purpose**: Attached because ECS requires a task role structurally; Nginx serving static files needs zero AWS permissions.

**Permissions**: none beyond what ECS itself requires to exist. Do not attach any managed policy "just in case."

---

## Role 4 — CI/CD Deploy Role (GitHub Actions)

**Name**: `graphforge-github-actions-deploy-role`

**Purpose**: Assumed by GitHub Actions via **OIDC federation** — no long-lived AWS access key stored as a GitHub secret, ever.

**Trust policy**: GitHub's OIDC provider, conditioned on this specific repository and branch:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": { "Federated": "arn:aws:iam::<account>:oidc-provider/token.actions.githubusercontent.com" },
    "Action": "sts:AssumeRoleWithWebIdentity",
    "Condition": {
      "StringEquals": { "token.actions.githubusercontent.com:aud": "sts.amazonaws.com" },
      "StringLike": { "token.actions.githubusercontent.com:sub": "repo:<org>/GraphForge:ref:refs/heads/master" }
    }
  }]
}
```

**Permissions**:

| Action | Resource | Why |
|---|---|---|
| `ecr:GetAuthorizationToken`, `ecr:BatchCheckLayerAvailability`, `ecr:PutImage`, `ecr:InitiateLayerUpload`, `ecr:UploadLayerPart`, `ecr:CompleteLayerUpload` | `graphforge-backend`, `graphforge-frontend` repos only | Pushing the images built in `07_CICD.md` |
| `ecs:RegisterTaskDefinition` | `*` (task definition registration has no resource-level restriction) | Creating a new revision per deploy |
| `ecs:UpdateService`, `ecs:DescribeServices` | The specific cluster/service ARNs for `graphforge-backend`/`graphforge-frontend` | Triggering and verifying the deployment |
| `ecs:RunTask` | Scoped to the migration task definition family only | The one-off Alembic migration step (`07_CICD.md`, `10_CODE_CHANGES.md` §6.2) |
| `iam:PassRole` | The two ECS Task Roles + the Task Execution Role, **and no others** | ECS requires the deploying principal to be allowed to pass these specific roles to the new task — this is the one IAM action the deploy role needs, tightly scoped so it can't hand out unrelated roles |

**Least-privilege rationale**: not `AdministratorAccess`, not `ecs:*`. Scoped to exactly the actions the pipeline in `07_CICD.md` performs, against exactly this project's resources.

---

## Role 5 (optional) — Secret Rotation Lambda Execution Role

Only needed if automated rotation (`06_SECRETS.md`) is implemented.

**Permissions**: `secretsmanager:PutSecretValue`, `secretsmanager:UpdateSecretVersionStage` on the specific secret being rotated, plus whatever network/credential access is needed to actually change the underlying credential (e.g. RDS master-user permission to `ALTER ROLE ... PASSWORD`, or Neo4j driver access to run the equivalent).

---

## Summary table

| Role | Assumed by | Scope |
|---|---|---|
| `graphforge-ecs-execution-role` | ECS agent | ECR pull (2 repos), CloudWatch Logs write (2 log groups), Secrets Manager read (named secrets) |
| `graphforge-backend-task-role` | Backend container at runtime | Bedrock Converse/InvokeModel on configured model ARNs only |
| `graphforge-frontend-task-role` | Frontend container at runtime | None |
| `graphforge-github-actions-deploy-role` | GitHub Actions, via OIDC (no stored key) | ECR push, ECS register/update/run-task, `iam:PassRole` on the three roles above only |
| (optional) rotation Lambda role | Secrets Manager rotation Lambda | Named secret + underlying credential-change access |

## See also

- `06_SECRETS.md` — the exact secret ARNs referenced by Role 1's `secretsmanager:GetSecretValue`
- `02_INFRASTRUCTURE.md` — where these roles attach (ECS task definitions)
- `07_CICD.md` — the pipeline that assumes Role 4
