"""Tests for resolving a pasted GitHub PR URL into a `pull_request` Subject
in the standalone AI Workspace flow (app.api.v1.routers.agent_runs).

Covers the P0 bug fix: previously a URL like
https://github.com/acme/widgets/pull/42 fell through to `resolve_freetext`,
producing `subject_type="freetext"` — which `REVIEW_MANIFEST`'s
`accepted_subject_types={"pull_request"}` rejects with
SubjectTypeMismatchError, making the AI Workspace "PR Review" card
non-functional for its stated purpose.

Real DB (repository/PullRequest lookups, via the transactional `db_session`
fixture), mocked I/O boundary: the GitHub API call and the access-token
lookup — the same boundary-mocking style `test_documentation_agent.py` uses.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.routers.agent_runs import (
    _GITHUB_PR_URL_RE,
    _resolve_pull_request_url_subject,
)
from app.core.exceptions import AppError, NotFoundError
from app.models.pull_request import PullRequest
from app.models.repository import Repository
from app.models.user import User


async def _make_user_and_repository(db: AsyncSession, *, owner: str = "acme", name: str = "widgets") -> Repository:
    user = User(
        id=uuid.uuid4(),
        email=f"{uuid.uuid4().hex}@example.com",
        hashed_password="x",
        full_name="Test User",
    )
    db.add(user)
    await db.flush()

    repository = Repository(
        user_id=user.id,
        github_repo_id=str(uuid.uuid4().int)[:10],
        owner=owner,
        name=name,
        full_name=f"{owner}/{name}",
        private=False,
        default_branch="main",
        html_url=f"https://github.com/{owner}/{name}",
    )
    db.add(repository)
    await db.flush()
    return repository


def test_github_pr_url_regex_matches_and_extracts() -> None:
    match = _GITHUB_PR_URL_RE.match("https://github.com/acme/widgets/pull/42")
    assert match is not None
    assert match["owner"] == "acme"
    assert match["repo"] == "widgets"
    assert match["number"] == "42"


def test_github_pr_url_regex_rejects_non_pr_urls() -> None:
    assert _GITHUB_PR_URL_RE.match("https://github.com/acme/widgets") is None
    assert _GITHUB_PR_URL_RE.match("https://gitlab.com/acme/widgets/-/merge_requests/1") is None
    assert _GITHUB_PR_URL_RE.match("not a url") is None


@pytest.mark.asyncio
async def test_resolve_rejects_a_non_github_pr_url(db_session: AsyncSession) -> None:
    with pytest.raises(NotFoundError):
        await _resolve_pull_request_url_subject(db_session, uuid.uuid4(), "https://example.com/not/a/pr")


@pytest.mark.asyncio
async def test_resolve_rejects_an_untracked_repository(db_session: AsyncSession) -> None:
    with pytest.raises(NotFoundError):
        await _resolve_pull_request_url_subject(
            db_session, uuid.uuid4(), "https://github.com/acme/widgets/pull/1"
        )


@pytest.mark.asyncio
async def test_resolve_reuses_an_already_tracked_pull_request(db_session: AsyncSession) -> None:
    """When a webhook already synced this PR, no GitHub API call is made —
    the existing row is reused as-is."""
    repository = await _make_user_and_repository(db_session)
    pull_request = PullRequest(
        repository_id=repository.id,
        github_pr_id="999",
        number=7,
        title="Add widget caching",
        state="open",
        is_draft=False,
        author_login="octocat",
        html_url="https://github.com/acme/widgets/pull/7",
        head_ref="feature/cache",
        head_sha="a" * 40,
        base_ref="main",
        github_created_at=datetime.now(UTC),
        github_updated_at=datetime.now(UTC),
    )
    db_session.add(pull_request)
    await db_session.flush()

    with patch("app.api.v1.routers.agent_runs.GitHubVersionControlProvider") as mock_provider_cls:
        subject = await _resolve_pull_request_url_subject(
            db_session, repository.user_id, "https://github.com/acme/widgets/pull/7"
        )

    mock_provider_cls.assert_not_called()
    assert subject.subject_type == "pull_request"
    assert subject.subject_id == f"pr:{pull_request.id}"
    assert "Add widget caching" in subject.display_name


@pytest.mark.asyncio
async def test_resolve_fetches_and_upserts_an_untracked_pull_request(db_session: AsyncSession) -> None:
    """No PullRequest row exists yet (no webhook has fired) — fetched from
    GitHub directly and persisted so the review agent has a `pull_request_id`
    to investigate."""
    repository = await _make_user_and_repository(db_session)

    pr_payload = {
        "id": 555,
        "number": 42,
        "title": "Fix flaky test",
        "state": "open",
        "merged": False,
        "draft": False,
        "user": {"login": "octocat"},
        "html_url": "https://github.com/acme/widgets/pull/42",
        "head": {"ref": "fix/flaky", "sha": "b" * 40},
        "base": {"ref": "main"},
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-02T00:00:00Z",
    }

    with (
        patch(
            "app.api.v1.routers.agent_runs.get_decrypted_access_token",
            new=AsyncMock(return_value="fake-token"),
        ),
        patch("app.api.v1.routers.agent_runs.GitHubVersionControlProvider") as mock_provider_cls,
    ):
        mock_provider_cls.return_value.get_pull_request = AsyncMock(return_value=pr_payload)
        subject = await _resolve_pull_request_url_subject(
            db_session, repository.user_id, "https://github.com/acme/widgets/pull/42"
        )

    mock_provider_cls.return_value.get_pull_request.assert_awaited_once_with(
        "acme", "widgets", 42, access_token="fake-token"
    )
    assert subject.subject_type == "pull_request"
    assert "Fix flaky test" in subject.display_name

    result = await db_session.execute(
        PullRequest.__table__.select().where(PullRequest.repository_id == repository.id)
    )
    row = result.mappings().one()
    assert row["number"] == 42
    assert row["github_pr_id"] == "555"
    assert row["title"] == "Fix flaky test"


@pytest.mark.asyncio
async def test_resolve_raises_not_found_when_github_has_no_such_pr(db_session: AsyncSession) -> None:
    repository = await _make_user_and_repository(db_session)

    with (
        patch(
            "app.api.v1.routers.agent_runs.get_decrypted_access_token",
            new=AsyncMock(return_value=None),
        ),
        patch("app.api.v1.routers.agent_runs.GitHubVersionControlProvider") as mock_provider_cls,
    ):
        mock_provider_cls.return_value.get_pull_request = AsyncMock(return_value=None)
        with pytest.raises(NotFoundError):
            await _resolve_pull_request_url_subject(
                db_session, repository.user_id, "https://github.com/acme/widgets/pull/999"
            )


@pytest.mark.asyncio
async def test_resolve_wraps_a_github_api_failure(db_session: AsyncSession) -> None:
    from app.integrations.github import GitHubApiError

    repository = await _make_user_and_repository(db_session)

    with (
        patch(
            "app.api.v1.routers.agent_runs.get_decrypted_access_token",
            new=AsyncMock(return_value=None),
        ),
        patch("app.api.v1.routers.agent_runs.GitHubVersionControlProvider") as mock_provider_cls,
    ):
        mock_provider_cls.return_value.get_pull_request = AsyncMock(
            side_effect=GitHubApiError("boom")
        )
        with pytest.raises(AppError) as exc_info:
            await _resolve_pull_request_url_subject(
                db_session, repository.user_id, "https://github.com/acme/widgets/pull/1"
            )
    assert exc_info.value.error_code == "github_pr_fetch_failed"
