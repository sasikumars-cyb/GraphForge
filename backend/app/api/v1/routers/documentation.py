"""Documentation Agent follow-up actions — the (optional) "Create
Documentation PR" step from a completed `review_documentation` run.

Deliberately a separate, explicit action rather than something the
Documentation Agent does itself: the agent only ever proposes Markdown
changes (see app.agents.documentation.agent's own docstring — "nothing is
ever written back to the repository automatically"). This endpoint is the
one place a human's explicit click turns a proposal into a real branch,
commit, and pull request — reusing the same `IGitWriteProvider` primitives
(`create_branch`/`create_commit`/`create_pull_request`) and the same
`PullRequest` upsert `CreatePullRequestAgent`/`webhook_service` already
use, so a documentation PR is indistinguishable, in storage, from any
other tracked PR.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.documentation.schemas import DocumentationReviewResult
from app.agents.git_ops.create_pull_request_agent import _upsert_pull_request
from app.api.v1.dependencies import get_current_user
from app.core.exceptions import AppError, NotFoundError
from app.database.session import get_db_session
from app.integrations.factory import create_git_write_provider
from app.integrations.github import GitHubApiError
from app.models.agent_step import AgentStep
from app.models.repository import Repository
from app.models.run import Run
from app.models.user import User
from app.services.github_service import get_decrypted_access_token

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/documentation", tags=["documentation"])


class CreateDocumentationPRResponse(BaseModel):
    pull_request_url: str
    branch_name: str
    files_changed: int


class CreatePRExecutionError(AppError):
    status_code = 502
    error_code = "documentation_pr_creation_failed"


async def _load_completed_result(
    db: AsyncSession, run_id: uuid.UUID, user_id: uuid.UUID
) -> tuple[Run, Repository, DocumentationReviewResult]:
    run_result = await db.execute(
        select(Run).where(
            Run.id == run_id, Run.user_id == user_id, Run.goal == "review_documentation"
        )
    )
    run = run_result.scalar_one_or_none()
    if run is None:
        raise NotFoundError(f"Documentation review run '{run_id}' not found for this account.")
    if run.status != "completed":
        raise CreatePRExecutionError(f"Run '{run_id}' is not completed (status: {run.status}).")

    step_result = await db.execute(
        select(AgentStep)
        .where(AgentStep.run_id == run.id)
        .order_by(AgentStep.created_at.desc())
        .limit(1)
    )
    step = step_result.scalar_one_or_none()
    if step is None or not step.result:
        raise NotFoundError(f"No result recorded for run '{run_id}'.")
    review = DocumentationReviewResult.model_validate(step.result)

    repo_result = await db.execute(
        select(Repository).where(
            Repository.full_name == review.repository_full_name, Repository.user_id == user_id
        )
    )
    repository = repo_result.scalar_one_or_none()
    if repository is None:
        raise NotFoundError(f"Repository '{review.repository_full_name}' is no longer tracked.")

    return run, repository, review


@router.post("/runs/{run_id}/create-pr", response_model=CreateDocumentationPRResponse)
async def create_documentation_pr(
    run_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> CreateDocumentationPRResponse:
    """Open a pull request applying every proposed update and new document
    from a completed `review_documentation` run, in one commit."""
    run, repository, review = await _load_completed_result(db, run_id, user.id)

    files = [
        {"path": u.file_path, "content": u.proposed_markdown} for u in review.proposed_updates
    ] + [
        {"path": d.file_path, "content": d.proposed_markdown} for d in review.proposed_new_documents
    ]
    if not files:
        raise CreatePRExecutionError("This run has no proposed updates or new documents to apply.")

    access_token = await get_decrypted_access_token(db, user.id)
    if access_token is None:
        raise CreatePRExecutionError("No GitHub connection found. Connect GitHub first.")

    owner, repo = repository.full_name.split("/", 1)
    branch_name = f"documentation/review-{str(run.id)[:8]}"
    vcs = create_git_write_provider()

    try:
        base_sha = await vcs.get_branch_sha(owner, repo, repository.default_branch, access_token)
        existing_branch_sha = await vcs.get_branch_sha_or_none(
            owner, repo, branch_name, access_token
        )
        if existing_branch_sha is None:
            await vcs.create_branch(owner, repo, branch_name, base_sha, access_token)

        await vcs.create_commit(
            owner,
            repo,
            branch_name,
            files,
            message="docs: apply Documentation Agent proposed changes",
            access_token=access_token,
        )

        try:
            pr_data = await vcs.create_pull_request(
                owner,
                repo,
                head=branch_name,
                base=repository.default_branch,
                title=f"Documentation updates for {repository.full_name}",
                body=review.summary or "Proposed by the Documentation Agent.",
                access_token=access_token,
            )
        except GitHubApiError as exc:
            if "already exists" not in str(exc).lower():
                raise
            pr_data = await vcs.get_pull_request_by_head(owner, repo, branch_name, access_token)
            if pr_data is None:
                raise CreatePRExecutionError(
                    f"GitHub reports a pull request already exists for '{branch_name}' "
                    f"but it could not be found: {exc}"
                ) from exc
    except GitHubApiError as exc:
        logger.warning("documentation_pr_creation_failed run_id=%s error=%s", run_id, exc)
        raise CreatePRExecutionError(f"Failed to create documentation pull request: {exc}") from exc

    pull_request = await _upsert_pull_request(db, repository.id, pr_data)
    await db.commit()

    logger.info(
        "documentation_pr_created run_id=%s repository=%s pr=%s",
        run_id,
        repository.full_name,
        pull_request.number,
    )
    return CreateDocumentationPRResponse(
        pull_request_url=pull_request.html_url,
        branch_name=branch_name,
        files_changed=len(files),
    )
