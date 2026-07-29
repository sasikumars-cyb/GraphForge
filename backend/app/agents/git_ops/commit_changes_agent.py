"""CommitChanges Agent — commits generated files to the execution branch.

Deterministic execution: no LLM. Reads the GeneratedCodeResult from the
generate_code stage and the BranchInfo from the create_branch stage, then
creates a single atomic commit via the Git Data API (trees + commits).

Idempotency: before committing, checks if the branch HEAD already has
a commit whose message matches. If so, returns the existing commit SHA.
Retries are safe — no duplicate commits.
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
from app.agents.git_ops.schemas import CommitInfo
from app.core.exceptions import AppError
from app.integrations.factory import create_git_write_provider
from app.integrations.github import GitHubApiError
from app.services.github_service import get_decrypted_access_token

logger = logging.getLogger(__name__)


class CommitChangesExecutionError(AppError):
    status_code = 502
    error_code = "commit_changes_execution_error"


class CommitChangesAgent:
    """Implements IAgent for goal=commit_changes. Stateless singleton."""

    async def run(self, context: AgentContext) -> AgentOutput:
        db = context.extras["db"]
        workflow = context.extras.get("workflow")
        subject_id = context.subject.subject_id

        if workflow is None:
            raise CommitChangesExecutionError("commit_changes requires a workflow context.")

        # --- Read prior stage results ---
        code_result = get_stage_result(workflow, "generate_code")
        if code_result is None:
            raise CommitChangesExecutionError(
                "No completed generate_code result found in workflow."
            )

        branch_result = get_stage_result(workflow, "create_branch")
        if branch_result is None:
            raise CommitChangesExecutionError(
                "No completed create_branch result found in workflow."
            )

        repository = code_result.get("repository", "")
        if not repository or "/" not in repository:
            raise CommitChangesExecutionError(
                f"Invalid repository in generate_code result: '{repository}'"
            )

        owner, repo = repository.split("/", 1)
        branch_name = branch_result.get("branch_name", "")
        commit_message = code_result.get("commit_message", "")
        files = code_result.get("files", [])

        if not branch_name:
            raise CommitChangesExecutionError("Missing branch_name in create_branch result.")
        if not commit_message:
            raise CommitChangesExecutionError("Missing commit_message in generate_code result.")
        if not files:
            raise CommitChangesExecutionError("No files in generate_code result.")

        # --- Get access token ---
        user_id = context.extras.get("user_id")
        if user_id is None:
            raise CommitChangesExecutionError("commit_changes requires user_id in context extras.")
        access_token = await get_decrypted_access_token(db, user_id)
        if access_token is None:
            raise CommitChangesExecutionError(
                "No GitHub connection found. Connect GitHub before running execution workflows."
            )

        vcs = create_git_write_provider()
        evidence: list[Evidence] = []

        # --- Idempotency: check if branch HEAD already has our commit ---
        try:
            head_sha = await vcs.get_branch_sha(owner, repo, branch_name, access_token)
            head_commit = await vcs.get_commit(owner, repo, head_sha, access_token)
            existing_message = head_commit.get("message", "")

            if existing_message == commit_message:
                logger.info(
                    "commit_changes_idempotent branch=%s commit=%s already applied",
                    branch_name,
                    head_sha[:8],
                )
                evidence.append(
                    Evidence(
                        kind="tool_call",
                        reference="github_get_commit",
                        summary=(
                            f"Branch '{branch_name}' HEAD ({head_sha[:8]}) already has "
                            f"commit message matching. Reused."
                        ),
                    )
                )

                result = CommitInfo(
                    repository=repository,
                    branch_name=branch_name,
                    commit_sha=head_sha,
                    files_changed=len(files),
                    commit_message=commit_message,
                    executive_summary=(
                        f"Commit already applied on '{branch_name}' ({head_sha[:8]})."
                    ),
                )

                return AgentOutput(
                    agent_id="commit_changes",
                    subject_id=subject_id,
                    confidence=Confidence(
                        score=0.95,
                        reasoning="Idempotent: commit already present on branch.",
                    ),
                    evidence=evidence,
                    result=result.model_dump(),
                )
        except GitHubApiError as exc:
            # If we can't check, proceed with the commit
            logger.warning("commit_changes_idempotency_check_failed error=%s", str(exc))

        # --- Build file entries for the Git Data API ---
        api_files = []
        for f in files:
            path = f.get("path", "")
            operation = f.get("operation", "create")
            content = f.get("content", "")

            if operation == "delete":
                api_files.append({"path": path, "content": None})
            else:
                api_files.append({"path": path, "content": content})

        evidence.append(
            Evidence(
                kind="tool_call",
                reference="prepare_file_tree",
                summary=f"Prepared {len(api_files)} file(s) for commit.",
            )
        )

        # --- Create atomic commit ---
        try:
            commit_sha = await vcs.create_commit(
                owner, repo, branch_name, api_files, commit_message, access_token
            )
        except GitHubApiError as exc:
            raise CommitChangesExecutionError(
                f"Failed to create commit on '{branch_name}': {exc}"
            ) from exc

        evidence.append(
            Evidence(
                kind="tool_call",
                reference="github_create_commit",
                summary=f"Created commit {commit_sha[:8]} on '{branch_name}'.",
            )
        )

        result = CommitInfo(
            repository=repository,
            branch_name=branch_name,
            commit_sha=commit_sha,
            files_changed=len(api_files),
            commit_message=commit_message,
            executive_summary=(
                f"Committed {len(api_files)} file(s) to '{branch_name}' " f"({commit_sha[:8]})."
            ),
        )

        logger.info(
            "commit_changes_completed repo=%s branch=%s sha=%s files=%d",
            repository,
            branch_name,
            commit_sha[:8],
            len(api_files),
        )

        return AgentOutput(
            agent_id="commit_changes",
            subject_id=subject_id,
            confidence=Confidence(
                score=0.95,
                reasoning="Deterministic commit via Git Data API.",
            ),
            evidence=evidence,
            result=result.model_dump(),
        )
