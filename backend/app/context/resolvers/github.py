"""GitHub Entry Resolver — KAN-27.

Resolves a GitHub-shaped entry point (a tracked repository id, or a pasted
pull request URL) into a canonical Subject, the same job
`app.context.resolvers.freetext` does for free text.

Unlike freetext, this resolver is not pure: identifying *which* tracked
Repository/PullRequest a caller means requires a real lookup (and, for an
untracked pull request, one GitHub API call to fetch and persist it) — the
same I/O boundary `ARCHITECTURE.md`'s Context Builder section describes an
Entry Resolver as owning. Both functions here were previously private
helpers inside `app.api.v1.routers.agent_runs`
(`_resolve_repository_subject`/`_resolve_pull_request_url_subject`) and are
moved here verbatim, not rewritten — this closes the gap between
`ARCHITECTURE.md`'s documented `GitHubEntryResolver` and what actually
existed (a real, working, but undiscoverable router-local implementation),
rather than building a second one.

Each function still delegates the actual Subject construction to the
existing agent-owned resolvers (`resolve_repository_subject` in
`app.agents.documentation.agent`, `resolve_pr_subject` in
`app.agents.review_adapter`) — this module's own job is strictly identity
resolution (which row does this reference mean, fetching it from GitHub if
GraphForge doesn't have it yet), matching the two-stage Entry
Resolver → Context Assembler split `ARCHITECTURE.md` describes.
"""

from __future__ import annotations

import re
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents._contract import Subject
from app.agents.documentation.agent import resolve_repository_subject
from app.agents.review_adapter import resolve_pr_subject
from app.core.exceptions import AppError, NotFoundError
from app.integrations.github import GitHubApiError, GitHubVersionControlProvider
from app.models.pull_request import PullRequest
from app.models.repository import Repository
from app.services.github_service import get_decrypted_access_token
from app.services.webhook_service import pull_request_fields_from_api_payload

# Matches https://github.com/{owner}/{repo}/pull/{number} — same pattern
# ReviewPage.tsx validates client-side before ever sending the request here.
GITHUB_PR_URL_RE = re.compile(
    r"^https://github\.com/(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+)/pull/(?P<number>\d+)/?$"
)


async def resolve_repository_id(
    db: AsyncSession, user_id: uuid.UUID, repository_id: str
) -> Subject:
    """Resolve a `repo:<uuid>` subject_reference to a Subject — the one
    place a repository-scoped goal (currently: review_documentation) turns
    a repository id into what the orchestrator needs. Scoped to `user_id`;
    raises NotFoundError (surfaced as 404) for an unknown or unowned id
    rather than a generic freetext fallback, since a wrong/stale repo id
    here should fail loudly, not silently run against the wrong subject.
    """
    try:
        parsed_id = uuid.UUID(repository_id)
    except ValueError as exc:
        raise NotFoundError(f"'{repository_id}' is not a valid repository id.") from exc
    result = await db.execute(
        select(Repository).where(Repository.id == parsed_id, Repository.user_id == user_id)
    )
    repository = result.scalar_one_or_none()
    if repository is None:
        raise NotFoundError(f"Repository '{repository_id}' not found for this account.")
    return resolve_repository_subject(repository)


async def resolve_pull_request_url(db: AsyncSession, user_id: uuid.UUID, url: str) -> Subject:
    """Resolve a pasted `https://github.com/{owner}/{repo}/pull/{n}` URL to
    a `pull_request` Subject — the missing piece that let a standalone AI
    Workspace "PR Review" submission reach `resolve_freetext` instead
    (producing `subject_type="freetext"`, which `REVIEW_MANIFEST`'s
    `accepted_subject_types={"pull_request"}` rejects with
    SubjectTypeMismatchError).

    Reuses the exact same orchestrator path every other goal uses — this
    only changes what `Subject` gets built, not how it's dispatched.

    Requires the target repository to already be tracked by this user
    (same ownership scoping as `resolve_repository_id`, needed here to
    know which stored GitHub access token to fetch the PR with). The
    underlying `PullRequest` row is found if a webhook already synced it,
    or upserted on the fly via a direct GitHub API call otherwise — a
    user reviewing a PR shouldn't have to wait for a webhook event first.
    """
    match = GITHUB_PR_URL_RE.match(url.strip())
    if not match:
        raise NotFoundError(
            f"'{url}' is not a GitHub pull request URL "
            "(expected https://github.com/{owner}/{repo}/pull/{number})."
        )
    owner, repo_name, number = match["owner"], match["repo"], int(match["number"])

    repo_result = await db.execute(
        select(Repository).where(
            Repository.user_id == user_id,
            func.lower(Repository.owner) == owner.lower(),
            func.lower(Repository.name) == repo_name.lower(),
        )
    )
    repository = repo_result.scalar_one_or_none()
    if repository is None:
        raise NotFoundError(
            f"Repository '{owner}/{repo_name}' is not tracked by this account. "
            "Track it under Repositories before reviewing one of its pull requests."
        )

    existing_result = await db.execute(
        select(PullRequest).where(
            PullRequest.repository_id == repository.id,
            PullRequest.number == number,
        )
    )
    pull_request = existing_result.scalar_one_or_none()
    if pull_request is not None:
        return resolve_pr_subject(
            pull_request.id, display_name=f"{owner}/{repo_name}#{number}: {pull_request.title}"
        )

    access_token = await get_decrypted_access_token(db, user_id)
    try:
        pr_payload = await GitHubVersionControlProvider().get_pull_request(
            owner, repo_name, number, access_token=access_token
        )
    except GitHubApiError as exc:
        raise AppError(
            f"Could not fetch {owner}/{repo_name}#{number} from GitHub: {exc}",
            status_code=502,
            error_code="github_pr_fetch_failed",
        ) from exc
    if pr_payload is None:
        raise NotFoundError(f"Pull request {owner}/{repo_name}#{number} was not found on GitHub.")

    fields = pull_request_fields_from_api_payload(pr_payload)
    pull_request = PullRequest(
        repository_id=repository.id,
        github_pr_id=str(pr_payload["id"]),
        **fields,
    )
    db.add(pull_request)
    await db.flush()

    return resolve_pr_subject(
        pull_request.id, display_name=f"{owner}/{repo_name}#{number}: {pull_request.title}"
    )
