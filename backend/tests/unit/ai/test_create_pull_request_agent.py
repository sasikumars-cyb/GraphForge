"""Unit tests for the CreatePullRequestAgent (Phase 5 PR #6).

Covers:
- Happy path: opens a new PR, persists a PullRequest row
- Idempotency: local reuse (already-tracked PR for the branch)
- Idempotency: GitHub-race reuse (GitHub reports "already exists")
- Error cases: missing workflow, missing commit_changes result, invalid
  repository, missing branch_name, missing user_id, missing access
  token, repository not tracked locally, generic GitHub API failure
- Manifest and registration
- Schema validation (PullRequestInfo)
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents._contract import AgentContext, Subject
from app.agents.git_ops.create_pull_request_agent import (
    CreatePullRequestAgent,
    CreatePullRequestExecutionError,
)
from app.agents.git_ops.manifests import CREATE_PULL_REQUEST_MANIFEST
from app.agents.git_ops.schemas import PullRequestInfo
from app.integrations.github import GitHubApiError
from app.models.pull_request import PullRequest

# asyncio_mode = "auto" (pyproject.toml) runs async defs directly — no
# per-test marker needed, and no module-level pytestmark, which would
# incorrectly tag the plain sync tests below too (manifest/schema checks).

WORKFLOW_ID = uuid.UUID("12345678-aaaa-bbbb-cccc-000000000001")
USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000099")
REPO_ID = uuid.UUID("00000000-0000-0000-0000-0000000000aa")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_step(result: dict | None) -> SimpleNamespace:
    return SimpleNamespace(result=result)


def _make_run(
    stage: str, status: str, result: dict | None, created_at: datetime | None = None
) -> SimpleNamespace:
    return SimpleNamespace(
        workflow_stage=stage,
        status=status,
        steps=[_make_step(result)] if result is not None else [],
        created_at=created_at or datetime.now(UTC),
    )


def _commit_result(
    repository: str = "acme/my-repo",
    branch_name: str = "graphforge/exec-12345678",
    commit_message: str = "feat: add rate limiting\n\nDetails here.",
) -> dict:
    return {
        "goal": "commit_changes",
        "executive_summary": "Committed 2 files.",
        "repository": repository,
        "branch_name": branch_name,
        "commit_sha": "abc123",
        "files_changed": 2,
        "commit_message": commit_message,
    }


def _code_result(executive_summary: str = "Adds a caching layer.") -> dict:
    return {
        "goal": "generate_code",
        "executive_summary": executive_summary,
        "repository": "acme/my-repo",
        "commit_message": "feat: add rate limiting",
        "files": [{"path": "a.py", "operation": "create", "content": "x"}],
        "confidence": 0.8,
    }


def _make_workflow(runs: list | None = None, title: str = "Add rate limiting") -> SimpleNamespace:
    return SimpleNamespace(id=WORKFLOW_ID, title=title, runs=runs or [])


def _make_context(workflow=None, user_id=USER_ID, db=None) -> AgentContext:
    subject = Subject(
        subject_id="freetext:exec",
        subject_type="freetext",
        display_name="Execute blueprint",
    )
    extras: dict = {"db": db if db is not None else AsyncMock()}
    if workflow is not None:
        extras["workflow"] = workflow
    if user_id is not None:
        extras["user_id"] = user_id
    return AgentContext(subject=subject, goal="create_pull_request", extras=extras)


def _repo_row(
    repo_id: uuid.UUID = REPO_ID,
    full_name: str = "acme/my-repo",
    default_branch: str = "main",
) -> SimpleNamespace:
    return SimpleNamespace(id=repo_id, full_name=full_name, default_branch=default_branch)


def _github_pr_payload(
    pr_id: int = 999,
    number: int = 42,
    title: str = "feat: add rate limiting",
    head_ref: str = "graphforge/exec-12345678",
    base_ref: str = "main",
) -> dict:
    return {
        "id": pr_id,
        "number": number,
        "title": title,
        "state": "open",
        "draft": False,
        "user": {"login": "graphforge-bot"},
        "html_url": f"https://github.com/acme/my-repo/pull/{number}",
        "head": {"ref": head_ref, "sha": "abc123"},
        "base": {"ref": base_ref},
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }


def _db_with_results(*results: object) -> AsyncMock:
    """An AsyncMock db whose .execute() returns each given scalar result
    (already the .scalar_one_or_none() value) in order, and whose
    .add()/.flush() are no-ops."""
    outcomes = list(results)

    async def _execute(*_args, **_kwargs):
        value = outcomes.pop(0)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = value
        return mock_result

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=_execute)
    db.add = MagicMock()
    db.flush = AsyncMock()
    return db


def _patched_agent(**vcs_methods):
    vcs = MagicMock()
    for name, mock in vcs_methods.items():
        setattr(vcs, name, mock)
    return patch(
        "app.agents.git_ops.create_pull_request_agent.GitHubVersionControlProvider",
        return_value=vcs,
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


async def test_creates_new_pull_request_and_persists_it() -> None:
    workflow = _make_workflow(
        runs=[
            _make_run("generate_code", "completed", _code_result()),
            _make_run("commit_changes", "completed", _commit_result()),
        ]
    )
    db = _db_with_results(_repo_row(), None, None)
    context = _make_context(workflow=workflow, db=db)

    with (
        patch(
            "app.agents.git_ops.create_pull_request_agent.get_decrypted_access_token",
            new=AsyncMock(return_value="tok"),
        ),
        _patched_agent(
            create_pull_request=AsyncMock(return_value=_github_pr_payload()),
        ),
    ):
        output = await CreatePullRequestAgent().run(context)

    result = PullRequestInfo.model_validate(output.result)
    assert result.github_pr_number == 42
    assert result.repository == "acme/my-repo"
    assert result.branch == "graphforge/exec-12345678"
    assert result.base_branch == "main"
    assert result.title == "feat: add rate limiting"
    assert result.state == "open"
    assert output.agent_id == "create_pull_request"
    assert any(e.kind == "tool_call" for e in output.evidence)
    assert db.add.call_count == 1
    added = db.add.call_args[0][0]
    assert isinstance(added, PullRequest)
    assert added.github_pr_id == "999"
    assert added.repository_id == REPO_ID


async def test_uses_default_branch_as_base() -> None:
    workflow = _make_workflow(runs=[_make_run("commit_changes", "completed", _commit_result())])
    db = _db_with_results(_repo_row(default_branch="develop"), None, None)
    context = _make_context(workflow=workflow, db=db)

    create_mock = AsyncMock(return_value=_github_pr_payload(base_ref="develop"))
    with (
        patch(
            "app.agents.git_ops.create_pull_request_agent.get_decrypted_access_token",
            new=AsyncMock(return_value="tok"),
        ),
        _patched_agent(create_pull_request=create_mock),
    ):
        await CreatePullRequestAgent().run(context)

    assert create_mock.call_args.kwargs["base"] == "develop"


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


async def test_reuses_existing_locally_tracked_pull_request() -> None:
    workflow = _make_workflow(runs=[_make_run("commit_changes", "completed", _commit_result())])
    existing = PullRequest(
        id=uuid.uuid4(),
        repository_id=REPO_ID,
        github_pr_id="999",
        number=42,
        title="feat: add rate limiting",
        state="open",
        is_draft=False,
        author_login="graphforge-bot",
        html_url="https://github.com/acme/my-repo/pull/42",
        head_ref="graphforge/exec-12345678",
        head_sha="abc123",
        base_ref="main",
        github_created_at=datetime(2026, 1, 1, tzinfo=UTC),
        github_updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    db = _db_with_results(_repo_row(), existing)
    context = _make_context(workflow=workflow, db=db)

    create_mock = AsyncMock()
    with (
        patch(
            "app.agents.git_ops.create_pull_request_agent.get_decrypted_access_token",
            new=AsyncMock(return_value="tok"),
        ),
        _patched_agent(create_pull_request=create_mock),
    ):
        output = await CreatePullRequestAgent().run(context)

    create_mock.assert_not_called()
    result = PullRequestInfo.model_validate(output.result)
    assert result.github_pr_number == 42
    assert "already tracked" in output.evidence[0].summary
    assert db.add.call_count == 0


async def test_reuses_pull_request_created_by_concurrent_request() -> None:
    workflow = _make_workflow(runs=[_make_run("commit_changes", "completed", _commit_result())])
    db = _db_with_results(_repo_row(), None, None)
    context = _make_context(workflow=workflow, db=db)

    with (
        patch(
            "app.agents.git_ops.create_pull_request_agent.get_decrypted_access_token",
            new=AsyncMock(return_value="tok"),
        ),
        _patched_agent(
            create_pull_request=AsyncMock(
                side_effect=GitHubApiError(
                    "GitHub pull request creation failed with status 422: "
                    "A pull request already exists for acme:graphforge/exec-12345678."
                )
            ),
            get_pull_request_by_head=AsyncMock(return_value=_github_pr_payload()),
        ),
    ):
        output = await CreatePullRequestAgent().run(context)

    result = PullRequestInfo.model_validate(output.result)
    assert result.github_pr_number == 42
    assert "concurrent request" in output.evidence[-1].summary


async def test_race_duplicate_but_pr_not_found_raises() -> None:
    workflow = _make_workflow(runs=[_make_run("commit_changes", "completed", _commit_result())])
    db = _db_with_results(_repo_row(), None)
    context = _make_context(workflow=workflow, db=db)

    with (
        patch(
            "app.agents.git_ops.create_pull_request_agent.get_decrypted_access_token",
            new=AsyncMock(return_value="tok"),
        ),
        _patched_agent(
            create_pull_request=AsyncMock(side_effect=GitHubApiError("... already exists ...")),
            get_pull_request_by_head=AsyncMock(return_value=None),
        ),
        pytest.raises(CreatePullRequestExecutionError),
    ):
        await CreatePullRequestAgent().run(context)


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


async def test_missing_workflow_raises() -> None:
    context = _make_context(workflow=None)
    with pytest.raises(CreatePullRequestExecutionError, match="requires a workflow"):
        await CreatePullRequestAgent().run(context)


async def test_missing_commit_changes_result_raises() -> None:
    workflow = _make_workflow(runs=[])
    context = _make_context(workflow=workflow)
    with pytest.raises(CreatePullRequestExecutionError, match="commit_changes"):
        await CreatePullRequestAgent().run(context)


async def test_invalid_repository_raises() -> None:
    workflow = _make_workflow(
        runs=[_make_run("commit_changes", "completed", _commit_result(repository="no-slash"))]
    )
    context = _make_context(workflow=workflow)
    with pytest.raises(CreatePullRequestExecutionError, match="Invalid repository"):
        await CreatePullRequestAgent().run(context)


async def test_missing_branch_name_raises() -> None:
    workflow = _make_workflow(
        runs=[_make_run("commit_changes", "completed", _commit_result(branch_name=""))]
    )
    context = _make_context(workflow=workflow)
    with pytest.raises(CreatePullRequestExecutionError, match="branch_name"):
        await CreatePullRequestAgent().run(context)


async def test_missing_user_id_raises() -> None:
    workflow = _make_workflow(runs=[_make_run("commit_changes", "completed", _commit_result())])
    context = _make_context(workflow=workflow, user_id=None)
    with pytest.raises(CreatePullRequestExecutionError, match="user_id"):
        await CreatePullRequestAgent().run(context)


async def test_missing_access_token_raises() -> None:
    workflow = _make_workflow(runs=[_make_run("commit_changes", "completed", _commit_result())])
    context = _make_context(workflow=workflow)
    with (
        patch(
            "app.agents.git_ops.create_pull_request_agent.get_decrypted_access_token",
            new=AsyncMock(return_value=None),
        ),
        pytest.raises(CreatePullRequestExecutionError, match="No GitHub connection"),
    ):
        await CreatePullRequestAgent().run(context)


async def test_repository_not_tracked_locally_raises() -> None:
    workflow = _make_workflow(runs=[_make_run("commit_changes", "completed", _commit_result())])
    db = _db_with_results(None)
    context = _make_context(workflow=workflow, db=db)
    with (
        patch(
            "app.agents.git_ops.create_pull_request_agent.get_decrypted_access_token",
            new=AsyncMock(return_value="tok"),
        ),
        pytest.raises(CreatePullRequestExecutionError, match="not tracked"),
    ):
        await CreatePullRequestAgent().run(context)


async def test_generic_github_failure_is_wrapped() -> None:
    workflow = _make_workflow(runs=[_make_run("commit_changes", "completed", _commit_result())])
    db = _db_with_results(_repo_row(), None)
    context = _make_context(workflow=workflow, db=db)

    with (
        patch(
            "app.agents.git_ops.create_pull_request_agent.get_decrypted_access_token",
            new=AsyncMock(return_value="tok"),
        ),
        _patched_agent(
            create_pull_request=AsyncMock(
                side_effect=GitHubApiError(
                    "GitHub pull request creation failed with status 403: Forbidden"
                )
            ),
        ),
        pytest.raises(CreatePullRequestExecutionError, match="Failed to create pull request"),
    ):
        await CreatePullRequestAgent().run(context)


# ---------------------------------------------------------------------------
# Manifest / registration / schema
# ---------------------------------------------------------------------------


def test_manifest_shape() -> None:
    assert CREATE_PULL_REQUEST_MANIFEST.agent_id == "create_pull_request"
    assert CREATE_PULL_REQUEST_MANIFEST.goals == frozenset({"create_pull_request"})
    assert CREATE_PULL_REQUEST_MANIFEST.accepted_subject_types == frozenset({"freetext"})
    assert CREATE_PULL_REQUEST_MANIFEST.cost_class == "cheap"
    assert CREATE_PULL_REQUEST_MANIFEST.output_schema_name == "PullRequestInfo"


def test_agent_is_registered() -> None:
    from app.agents.setup import register_agents
    from app.orchestrator.registry import global_registry

    register_agents()
    entry = global_registry.get("create_pull_request")
    assert entry is not None
    manifest, agent = entry
    assert manifest.agent_id == "create_pull_request"
    assert isinstance(agent, CreatePullRequestAgent)


def test_pull_request_info_schema_validation() -> None:
    info = PullRequestInfo(
        pull_request_id=str(uuid.uuid4()),
        github_pr_number=1,
        html_url="https://github.com/acme/my-repo/pull/1",
        repository="acme/my-repo",
        branch="graphforge/exec-1",
        base_branch="main",
        title="feat: x",
        body="body text",
        state="open",
        created_at="2026-01-01T00:00:00+00:00",
    )
    assert info.goal == "create_pull_request"
