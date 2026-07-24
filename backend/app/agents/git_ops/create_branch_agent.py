"""CreateBranch Agent — creates an execution branch on GitHub.

Deterministic execution: no LLM, no graph queries. Reads the repository
from the generate_code stage result, gets the default branch HEAD SHA,
and creates `graphforge/exec-{workflow_id_short}`.

Idempotency: if the branch already exists, returns the existing SHA
instead of failing. Retries are safe.
"""

from __future__ import annotations

import logging

from app.agents._contract import (
    AgentContext,
    AgentOutput,
    Confidence,
    Evidence,
)
from app.agents.git_ops._artifact_reader import get_stage_result
from app.agents.git_ops.schemas import BranchInfo
from app.core.exceptions import AppError
from app.integrations.github import GitHubApiError, GitHubVersionControlProvider
from app.services.github_service import get_decrypted_access_token

logger = logging.getLogger(__name__)


class CreateBranchExecutionError(AppError):
    status_code = 502
    error_code = "create_branch_execution_error"


class CreateBranchAgent:
    """Implements IAgent for goal=create_branch. Stateless singleton."""

    async def run(self, context: AgentContext) -> AgentOutput:
        db = context.extras["db"]
        workflow = context.extras.get("workflow")
        subject_id = context.subject.subject_id

        if workflow is None:
            raise CreateBranchExecutionError(
                "create_branch requires a workflow context."
            )

        # --- Read generate_code result ---
        code_result = get_stage_result(workflow, "generate_code")
        if code_result is None:
            raise CreateBranchExecutionError(
                "No completed generate_code result found in workflow."
            )

        repository = code_result.get("repository", "")
        if not repository or "/" not in repository:
            raise CreateBranchExecutionError(
                f"Invalid repository in generate_code result: '{repository}'"
            )

        owner, repo = repository.split("/", 1)
        workflow_id_short = str(workflow.id).split("-")[0]
        branch_name = f"graphforge/exec-{workflow_id_short}"

        # --- Get access token ---
        user_id = context.extras.get("user_id")
        if user_id is None:
            raise CreateBranchExecutionError(
                "create_branch requires user_id in context extras."
            )
        access_token = await get_decrypted_access_token(db, user_id)
        if access_token is None:
            raise CreateBranchExecutionError(
                "No GitHub connection found. Connect GitHub before running execution workflows."
            )

        vcs = GitHubVersionControlProvider()
        evidence: list[Evidence] = []

        # --- Idempotency: check if branch already exists ---
        try:
            existing_sha = await vcs.get_branch_sha_or_none(
                owner, repo, branch_name, access_token
            )
        except GitHubApiError as exc:
            raise CreateBranchExecutionError(
                f"Failed to check existing branch: {exc}"
            ) from exc

        if existing_sha is not None:
            logger.info(
                "create_branch_idempotent branch=%s already exists sha=%s",
                branch_name, existing_sha,
            )
            evidence.append(Evidence(
                kind="tool_call",
                reference="github_get_ref",
                summary=f"Branch '{branch_name}' already exists (SHA: {existing_sha[:8]}). Reused.",
            ))
            base_sha = existing_sha
        else:
            # --- Get default branch HEAD ---
            try:
                repo_data = await _get_default_branch(vcs, owner, repo, access_token)
                default_branch = repo_data
                base_sha = await vcs.get_branch_sha(owner, repo, default_branch, access_token)
            except GitHubApiError as exc:
                raise CreateBranchExecutionError(
                    f"Failed to get default branch HEAD: {exc}"
                ) from exc

            evidence.append(Evidence(
                kind="tool_call",
                reference="github_get_ref",
                summary=f"Retrieved HEAD of '{default_branch}': {base_sha[:8]}",
            ))

            # --- Create branch ---
            try:
                await vcs.create_branch(owner, repo, branch_name, base_sha, access_token)
            except GitHubApiError as exc:
                # 422 with "Reference already exists" is a race condition —
                # treat as idempotent success
                if "already exists" in str(exc).lower():
                    logger.info(
                        "create_branch_race_idempotent branch=%s", branch_name
                    )
                    evidence.append(Evidence(
                        kind="tool_call",
                        reference="github_create_ref",
                        summary=f"Branch '{branch_name}' created by concurrent request. Reused.",
                    ))
                else:
                    raise CreateBranchExecutionError(
                        f"Failed to create branch '{branch_name}': {exc}"
                    ) from exc
            else:
                evidence.append(Evidence(
                    kind="tool_call",
                    reference="github_create_ref",
                    summary=f"Created branch '{branch_name}' from {base_sha[:8]}",
                ))

        result = BranchInfo(
            repository=repository,
            branch_name=branch_name,
            base_sha=base_sha,
            executive_summary=f"Branch '{branch_name}' ready on {repository}.",
        )

        logger.info(
            "create_branch_completed repo=%s branch=%s sha=%s",
            repository, branch_name, base_sha[:8],
        )

        return AgentOutput(
            agent_id="create_branch",
            subject_id=subject_id,
            confidence=Confidence(score=0.95, reasoning="Deterministic branch creation."),
            evidence=evidence,
            result=result.model_dump(),
        )


async def _get_default_branch(
    vcs: GitHubVersionControlProvider,
    owner: str,
    repo: str,
    access_token: str | None,
) -> str:
    """Get the default branch name for a repository."""
    from app.integrations.github import _github_get

    data = await _github_get(f"/repos/{owner}/{repo}", access_token)
    return str(data.get("default_branch", "main"))
