"""CreatePullRequest Agent — opens a GitHub pull request for the execution
branch and persists it as a `PullRequest` record.

Deterministic execution: no LLM. Reads the CommitInfo from the
commit_changes stage (repository, branch, commit message) and the
tracked Repository row (for its default branch), then opens a PR via the
Git REST API and upserts the existing `pull_requests` table — the same
table `webhook_service.handle_pull_request_event` populates from GitHub
webhooks, so the result is addressable afterward as `pr:<uuid>` exactly
like any other tracked PR.

Idempotency: checked twice. First locally — if a PullRequest already
exists for this repository+branch, it's reused without calling GitHub at
all. Second against GitHub itself — if a concurrent/retried run races us
and GitHub reports "a pull request already exists", the existing PR is
looked up and upserted instead of failing. Retries are always safe.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents._contract import (
    AgentContext,
    AgentOutput,
    Confidence,
    Evidence,
)
from app.agents.git_ops._artifact_reader import get_stage_result
from app.agents.git_ops.schemas import PullRequestInfo
from app.core.exceptions import AppError
from app.integrations.github import GitHubApiError, GitHubVersionControlProvider
from app.models.pull_request import PullRequest
from app.models.repository import Repository
from app.services.github_service import get_decrypted_access_token
from app.services.webhook_service import _pull_request_state

logger = logging.getLogger(__name__)

_DEFAULT_TITLE = "GraphForge: automated changes"


class CreatePullRequestExecutionError(AppError):
    status_code = 502
    error_code = "create_pull_request_execution_error"


class CreatePullRequestAgent:
    """Implements IAgent for goal=create_pull_request. Stateless singleton."""

    async def run(self, context: AgentContext) -> AgentOutput:
        db = context.extras["db"]
        workflow = context.extras.get("workflow")
        subject_id = context.subject.subject_id

        if workflow is None:
            raise CreatePullRequestExecutionError(
                "create_pull_request requires a workflow context."
            )

        # --- Read prior stage results ---
        commit_result = get_stage_result(workflow, "commit_changes")
        if commit_result is None:
            raise CreatePullRequestExecutionError(
                "No completed commit_changes result found in workflow."
            )

        repository = commit_result.get("repository", "")
        if not repository or "/" not in repository:
            raise CreatePullRequestExecutionError(
                f"Invalid repository in commit_changes result: '{repository}'"
            )

        owner, repo = repository.split("/", 1)
        branch_name = commit_result.get("branch_name", "")
        commit_message = commit_result.get("commit_message", "")

        if not branch_name:
            raise CreatePullRequestExecutionError("Missing branch_name in commit_changes result.")

        code_result = get_stage_result(workflow, "generate_code") or {}
        title = (commit_message.splitlines()[0] if commit_message else "") or _DEFAULT_TITLE
        body = code_result.get("executive_summary") or commit_message or _DEFAULT_TITLE

        # --- Get access token ---
        user_id = context.extras.get("user_id")
        if user_id is None:
            raise CreatePullRequestExecutionError(
                "create_pull_request requires user_id in context extras."
            )
        access_token = await get_decrypted_access_token(db, user_id)
        if access_token is None:
            raise CreatePullRequestExecutionError(
                "No GitHub connection found. Connect GitHub before running execution workflows."
            )

        # --- Resolve the locally tracked Repository (needed for the FK on
        # PullRequest.repository_id, and for its known default branch) ---
        repo_row_result = await db.execute(
            select(Repository).where(
                Repository.user_id == user_id, Repository.full_name == repository
            )
        )
        repo_row = repo_row_result.scalar_one_or_none()
        if repo_row is None:
            raise CreatePullRequestExecutionError(
                f"Repository '{repository}' is not tracked. Select it under "
                "Repositories before running execution workflows."
            )
        base_branch = repo_row.default_branch

        evidence: list[Evidence] = []

        # --- Idempotency: reuse an already-persisted PR for this branch ---
        existing_result = await db.execute(
            select(PullRequest)
            .where(
                PullRequest.repository_id == repo_row.id,
                PullRequest.head_ref == branch_name,
            )
            .order_by(PullRequest.created_at.desc())
            .limit(1)
        )
        existing_pr = existing_result.scalar_one_or_none()
        if existing_pr is not None:
            logger.info(
                "create_pull_request_idempotent repo=%s branch=%s pr=%s already tracked",
                repository,
                branch_name,
                existing_pr.number,
            )
            evidence.append(
                Evidence(
                    kind="tool_call",
                    reference="pull_requests_lookup",
                    summary=(
                        f"Pull request #{existing_pr.number} already tracked for "
                        f"branch '{branch_name}'. Reused."
                    ),
                )
            )
            return _build_output(subject_id, existing_pr, repository, body, evidence)

        vcs = GitHubVersionControlProvider()

        # --- Create the PR on GitHub (or recover from a concurrent create) ---
        pr_data: dict[str, Any] | None
        try:
            pr_data = await vcs.create_pull_request(
                owner,
                repo,
                head=branch_name,
                base=base_branch,
                title=title,
                body=body,
                access_token=access_token,
            )
        except GitHubApiError as exc:
            if "already exists" in str(exc).lower():
                pr_data = await vcs.get_pull_request_by_head(owner, repo, branch_name, access_token)
                if pr_data is None:
                    raise CreatePullRequestExecutionError(
                        f"GitHub reports a pull request already exists for "
                        f"'{branch_name}' but it could not be found: {exc}"
                    ) from exc
                logger.info(
                    "create_pull_request_race_idempotent repo=%s branch=%s",
                    repository,
                    branch_name,
                )
                evidence.append(
                    Evidence(
                        kind="tool_call",
                        reference="github_list_pulls",
                        summary=(
                            f"Pull request #{pr_data['number']} for '{branch_name}' was "
                            "created by a concurrent request. Reused."
                        ),
                    )
                )
            else:
                raise CreatePullRequestExecutionError(
                    f"Failed to create pull request for '{branch_name}' -> "
                    f"'{base_branch}' on {repository}: {exc}"
                ) from exc
        else:
            evidence.append(
                Evidence(
                    kind="tool_call",
                    reference="github_create_pull_request",
                    summary=f"Opened pull request #{pr_data['number']} for '{branch_name}'.",
                )
            )

        # Reached only via the `else` branch (fresh create) or a successful
        # race-recovery lookup above — both already guarantee a dict, but
        # the try/except's shared `pr_data` type still carries `| None`.
        # An explicit raise (not `assert`) so this still fails loudly and
        # clearly if the interpreter is ever run with `-O`/`-OO`, which
        # strips `assert` statements entirely.
        if pr_data is None:
            raise CreatePullRequestExecutionError(
                f"Internal error: no pull request data available for branch "
                f"'{branch_name}' after create/recovery."
            )
        pull_request = await _upsert_pull_request(db, repo_row.id, pr_data)

        logger.info(
            "create_pull_request_completed repo=%s branch=%s pr=%s",
            repository,
            branch_name,
            pull_request.number,
        )

        return _build_output(subject_id, pull_request, repository, body, evidence)


async def _upsert_pull_request(
    db: AsyncSession, repository_id: uuid.UUID, pr_data: dict[str, Any]
) -> PullRequest:
    """Insert or update the `pull_requests` row for this GitHub PR — the
    exact upsert `webhook_service.handle_pull_request_event` already
    performs from webhook events, reused here so a PR opened by this
    agent is indistinguishable, in storage, from one GitHub notified us
    about."""
    github_pr_id = str(pr_data["id"])
    fields = {
        "number": pr_data["number"],
        "title": pr_data["title"],
        "state": _pull_request_state(pr_data),
        "is_draft": pr_data.get("draft", False),
        "author_login": pr_data["user"]["login"],
        "html_url": pr_data["html_url"],
        "head_ref": pr_data["head"]["ref"],
        "head_sha": pr_data["head"]["sha"],
        "base_ref": pr_data["base"]["ref"],
        "github_created_at": _parse_github_timestamp(pr_data["created_at"]),
        "github_updated_at": _parse_github_timestamp(pr_data["updated_at"]),
    }

    existing_result = await db.execute(
        select(PullRequest).where(
            PullRequest.repository_id == repository_id,
            PullRequest.github_pr_id == github_pr_id,
        )
    )
    pull_request = existing_result.scalar_one_or_none()

    if pull_request is None:
        pull_request = PullRequest(repository_id=repository_id, github_pr_id=github_pr_id, **fields)
        db.add(pull_request)
    else:
        for field, value in fields.items():
            setattr(pull_request, field, value)

    await db.flush()
    return pull_request


def _parse_github_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _build_output(
    subject_id: str,
    pull_request: PullRequest,
    repository: str,
    body: str,
    evidence: list[Evidence],
) -> AgentOutput:
    result = PullRequestInfo(
        pull_request_id=str(pull_request.id),
        github_pr_number=pull_request.number,
        html_url=pull_request.html_url,
        repository=repository,
        branch=pull_request.head_ref,
        base_branch=pull_request.base_ref,
        title=pull_request.title,
        body=body,
        state=pull_request.state,
        created_at=pull_request.github_created_at.isoformat(),
        executive_summary=f"Pull request #{pull_request.number} ready for review.",
    )

    return AgentOutput(
        agent_id="create_pull_request",
        subject_id=subject_id,
        confidence=Confidence(
            score=0.95,
            reasoning="Deterministic PR creation via GitHub REST API.",
        ),
        evidence=evidence,
        result=result.model_dump(),
    )
