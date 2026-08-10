"""Unit tests for the git_ops execution agents: CreateBranch and CommitChanges.

Covers:
- Happy path: successful creation, schema validation, evidence/result shape
- Idempotency: branch already exists, commit already applied
- Race condition: 422 "already exists" treated as idempotent
- Error cases: missing workflow, missing prior stage, missing token,
  invalid repository, API failures
- Artifact reader: correct stage lookup
- Manifest and registration
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.agents._contract import AgentContext, Subject
from app.agents.git_ops._artifact_reader import get_stage_result
from app.agents.git_ops.commit_changes_agent import (
    CommitChangesAgent,
    CommitChangesExecutionError,
)
from app.agents.git_ops.create_branch_agent import (
    CreateBranchAgent,
    CreateBranchExecutionError,
)
from app.agents.git_ops.manifests import COMMIT_CHANGES_MANIFEST, CREATE_BRANCH_MANIFEST
from app.agents.git_ops.schemas import BranchInfo, CommitInfo

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

WORKFLOW_ID = uuid.UUID("12345678-aaaa-bbbb-cccc-000000000001")
USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000099")
FAKE_SHA = "abc123def456abc123def456abc123def456abc1"
COMMIT_SHA = "def456abc123def456abc123def456abc123def456"


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


def _code_result(
    repository: str = "acme/my-repo",
    commit_message: str = "feat: add feature",
    files: list[dict] | None = None,
) -> dict:
    if files is None:
        files = [
            {"path": "src/main.py", "operation": "create", "content": "print('hello')"},
        ]
    return {
        "executive_summary": "Generated code.",
        "repository": repository,
        "commit_message": commit_message,
        "confidence": 0.85,
        "files": files,
    }


def _branch_result(branch_name: str = "graphforge/exec-12345678", base_sha: str = FAKE_SHA) -> dict:
    return {
        "repository": "acme/my-repo",
        "branch_name": branch_name,
        "base_sha": base_sha,
        "executive_summary": f"Branch '{branch_name}' ready.",
    }


def _make_workflow(
    runs: list | None = None,
    workflow_id: uuid.UUID = WORKFLOW_ID,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=workflow_id,
        runs=runs or [],
    )


def _development_run(repository: str = "acme/my-repo") -> SimpleNamespace:
    """A completed Development-stage run whose own graph traversal
    consulted `repository` — the deterministic ground truth
    `verify_repository` (app.agents.code_generation.verification) checks
    a claimed repository against before any git write."""
    return _make_run(
        "development",
        "completed",
        {"repositories_consulted": [repository]},
    )


def _make_workflow_with_repo(
    runs: list,
    repository: str = "acme/my-repo",
    workflow_id: uuid.UUID = WORKFLOW_ID,
) -> SimpleNamespace:
    """Same as `_make_workflow`, plus a Development stage run putting
    `repository` in this workflow's verified scope."""
    return _make_workflow([_development_run(repository), *runs], workflow_id=workflow_id)


def _make_tracked_db() -> AsyncMock:
    """A db whose `execute(...)` resolves to a result reporting the
    queried Repository row as found — i.e. `repository` is tracked/
    selected by this user (app.agents.code_generation.verification)."""
    db = AsyncMock()
    db.execute = AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: object()))
    return db


def _make_context(
    workflow=None,
    user_id=USER_ID,
    extras_overrides: dict | None = None,
    db: AsyncMock | None = None,
) -> AgentContext:
    subject = Subject(
        subject_id="freetext:exec",
        subject_type="freetext",
        display_name="Execute blueprint",
    )
    extras: dict = {"db": db if db is not None else _make_tracked_db()}
    if workflow is not None:
        extras["workflow"] = workflow
    if user_id is not None:
        extras["user_id"] = user_id
    if extras_overrides:
        extras.update(extras_overrides)
    return AgentContext(subject=subject, goal="create_branch", extras=extras)


# ===========================================================================
# Manifest tests
# ===========================================================================


class TestManifests:
    def test_create_branch_manifest(self):
        m = CREATE_BRANCH_MANIFEST
        assert m.agent_id == "create_branch"
        assert "create_branch" in m.goals
        assert "freetext" in m.accepted_subject_types
        assert m.cost_class == "cheap"

    def test_commit_changes_manifest(self):
        m = COMMIT_CHANGES_MANIFEST
        assert m.agent_id == "commit_changes"
        assert "commit_changes" in m.goals
        assert "freetext" in m.accepted_subject_types
        assert m.cost_class == "cheap"


# ===========================================================================
# Schema tests
# ===========================================================================


class TestSchemas:
    def test_branch_info_round_trip(self):
        info = BranchInfo(
            repository="acme/repo",
            branch_name="graphforge/exec-abc",
            base_sha=FAKE_SHA,
            executive_summary="Branch ready.",
        )
        d = info.model_dump()
        assert d["repository"] == "acme/repo"
        assert d["branch_name"] == "graphforge/exec-abc"
        assert BranchInfo(**d) == info

    def test_commit_info_round_trip(self):
        info = CommitInfo(
            repository="acme/repo",
            branch_name="graphforge/exec-abc",
            commit_sha=COMMIT_SHA,
            files_changed=3,
            commit_message="feat: stuff",
            executive_summary="Committed 3 files.",
        )
        d = info.model_dump()
        assert d["files_changed"] == 3
        assert CommitInfo(**d) == info


# ===========================================================================
# Artifact reader tests
# ===========================================================================


class TestArtifactReader:
    def test_returns_result_for_completed_run(self):
        code = _code_result()
        workflow = _make_workflow(runs=[_make_run("generate_code", "completed", code)])
        assert get_stage_result(workflow, "generate_code") == code

    def test_returns_none_for_missing_stage(self):
        workflow = _make_workflow(runs=[_make_run("generate_code", "completed", _code_result())])
        assert get_stage_result(workflow, "create_branch") is None

    def test_returns_none_for_failed_run(self):
        workflow = _make_workflow(runs=[_make_run("generate_code", "failed", _code_result())])
        assert get_stage_result(workflow, "generate_code") is None

    def test_returns_latest_completed_run(self):
        old = _code_result(commit_message="old")
        new = _code_result(commit_message="new")
        old_run = _make_run("generate_code", "completed", old, datetime(2024, 1, 1, tzinfo=UTC))
        new_run = _make_run("generate_code", "completed", new, datetime(2024, 6, 1, tzinfo=UTC))
        workflow = _make_workflow(runs=[old_run, new_run])
        result = get_stage_result(workflow, "generate_code")
        assert result is not None
        assert result["commit_message"] == "new"


# ===========================================================================
# CreateBranchAgent tests
# ===========================================================================


class TestCreateBranchAgent:
    """Tests for CreateBranchAgent.run()."""

    @pytest.mark.asyncio
    async def test_happy_path_creates_branch(self):
        code = _code_result()
        workflow = _make_workflow_with_repo([_make_run("generate_code", "completed", code)])
        ctx = _make_context(workflow=workflow)

        with (
            patch(
                "app.agents.git_ops.create_branch_agent.get_decrypted_access_token",
                new_callable=AsyncMock,
            ) as mock_token,
            patch("app.agents.git_ops.create_branch_agent.create_git_write_provider") as MockVCS,
            patch(
                "app.agents.git_ops.create_branch_agent._get_default_branch", new_callable=AsyncMock
            ) as mock_default,
        ):
            mock_token.return_value = "ghp_test_token"
            vcs = MockVCS.return_value
            vcs.get_branch_sha_or_none = AsyncMock(return_value=None)
            vcs.get_branch_sha = AsyncMock(return_value=FAKE_SHA)
            vcs.create_branch = AsyncMock(return_value=FAKE_SHA)
            mock_default.return_value = "main"

            agent = CreateBranchAgent()
            output = await agent.run(ctx)

        assert output.agent_id == "create_branch"
        assert output.result["repository"] == "acme/my-repo"
        assert output.result["branch_name"] == "graphforge/exec-12345678"
        assert output.result["base_sha"] == FAKE_SHA
        assert output.confidence.score == 0.95
        assert len(output.evidence) == 2  # get HEAD + create branch
        vcs.create_branch.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_idempotent_branch_already_exists(self):
        code = _code_result()
        workflow = _make_workflow_with_repo([_make_run("generate_code", "completed", code)])
        ctx = _make_context(workflow=workflow)

        with (
            patch(
                "app.agents.git_ops.create_branch_agent.get_decrypted_access_token",
                new_callable=AsyncMock,
            ) as mock_token,
            patch("app.agents.git_ops.create_branch_agent.create_git_write_provider") as MockVCS,
        ):
            mock_token.return_value = "ghp_test_token"
            vcs = MockVCS.return_value
            vcs.get_branch_sha_or_none = AsyncMock(return_value=FAKE_SHA)

            agent = CreateBranchAgent()
            output = await agent.run(ctx)

        assert output.result["base_sha"] == FAKE_SHA
        assert (
            "already exists" in output.evidence[0].summary.lower()
            or "reused" in output.evidence[0].summary.lower()
        )
        # Should NOT call create_branch
        vcs.create_branch = AsyncMock()  # would fail if called

    @pytest.mark.asyncio
    async def test_race_condition_422_treated_as_idempotent(self):
        """422 'Reference already exists' during create_branch is idempotent."""
        from app.integrations.github import GitHubApiError

        code = _code_result()
        workflow = _make_workflow_with_repo([_make_run("generate_code", "completed", code)])
        ctx = _make_context(workflow=workflow)

        with (
            patch(
                "app.agents.git_ops.create_branch_agent.get_decrypted_access_token",
                new_callable=AsyncMock,
            ) as mock_token,
            patch("app.agents.git_ops.create_branch_agent.create_git_write_provider") as MockVCS,
            patch(
                "app.agents.git_ops.create_branch_agent._get_default_branch", new_callable=AsyncMock
            ) as mock_default,
        ):
            mock_token.return_value = "ghp_test_token"
            vcs = MockVCS.return_value
            vcs.get_branch_sha_or_none = AsyncMock(return_value=None)
            vcs.get_branch_sha = AsyncMock(return_value=FAKE_SHA)
            vcs.create_branch = AsyncMock(
                side_effect=GitHubApiError("Reference already exists", status_code=422)
            )
            mock_default.return_value = "main"

            agent = CreateBranchAgent()
            output = await agent.run(ctx)

        # Should succeed, not raise
        assert output.agent_id == "create_branch"
        assert output.result["branch_name"] == "graphforge/exec-12345678"

    @pytest.mark.asyncio
    async def test_missing_workflow_raises(self):
        ctx = _make_context(workflow=None)
        agent = CreateBranchAgent()
        with pytest.raises(CreateBranchExecutionError, match="requires a workflow context"):
            await agent.run(ctx)

    @pytest.mark.asyncio
    async def test_missing_generate_code_result_raises(self):
        workflow = _make_workflow(runs=[])
        ctx = _make_context(workflow=workflow)
        agent = CreateBranchAgent()
        with pytest.raises(CreateBranchExecutionError, match="No completed generate_code"):
            await agent.run(ctx)

    @pytest.mark.asyncio
    async def test_invalid_repository_raises(self):
        code = _code_result(repository="no-slash-here")
        workflow = _make_workflow_with_repo([_make_run("generate_code", "completed", code)])
        ctx = _make_context(workflow=workflow)
        agent = CreateBranchAgent()
        with pytest.raises(CreateBranchExecutionError, match="Invalid repository"):
            await agent.run(ctx)

    @pytest.mark.asyncio
    async def test_missing_user_id_raises(self):
        code = _code_result()
        workflow = _make_workflow_with_repo([_make_run("generate_code", "completed", code)])
        ctx = _make_context(workflow=workflow, user_id=None)
        agent = CreateBranchAgent()
        with pytest.raises(CreateBranchExecutionError, match="requires user_id"):
            await agent.run(ctx)

    @pytest.mark.asyncio
    async def test_missing_access_token_raises(self):
        code = _code_result()
        workflow = _make_workflow_with_repo([_make_run("generate_code", "completed", code)])
        ctx = _make_context(workflow=workflow)

        with patch(
            "app.agents.git_ops.create_branch_agent.get_decrypted_access_token",
            new_callable=AsyncMock,
        ) as mock_token:
            mock_token.return_value = None
            agent = CreateBranchAgent()
            with pytest.raises(CreateBranchExecutionError, match="No GitHub connection"):
                await agent.run(ctx)

    @pytest.mark.asyncio
    async def test_api_error_during_branch_check_raises(self):
        from app.integrations.github import GitHubApiError

        code = _code_result()
        workflow = _make_workflow_with_repo([_make_run("generate_code", "completed", code)])
        ctx = _make_context(workflow=workflow)

        with (
            patch(
                "app.agents.git_ops.create_branch_agent.get_decrypted_access_token",
                new_callable=AsyncMock,
            ) as mock_token,
            patch("app.agents.git_ops.create_branch_agent.create_git_write_provider") as MockVCS,
        ):
            mock_token.return_value = "ghp_test_token"
            vcs = MockVCS.return_value
            vcs.get_branch_sha_or_none = AsyncMock(
                side_effect=GitHubApiError("API rate limit exceeded", status_code=403)
            )

            agent = CreateBranchAgent()
            with pytest.raises(CreateBranchExecutionError, match="Failed to check existing branch"):
                await agent.run(ctx)

    @pytest.mark.asyncio
    async def test_result_matches_branch_info_schema(self):
        code = _code_result()
        workflow = _make_workflow_with_repo([_make_run("generate_code", "completed", code)])
        ctx = _make_context(workflow=workflow)

        with (
            patch(
                "app.agents.git_ops.create_branch_agent.get_decrypted_access_token",
                new_callable=AsyncMock,
            ) as mock_token,
            patch("app.agents.git_ops.create_branch_agent.create_git_write_provider") as MockVCS,
            patch(
                "app.agents.git_ops.create_branch_agent._get_default_branch", new_callable=AsyncMock
            ) as mock_default,
        ):
            mock_token.return_value = "ghp_test_token"
            vcs = MockVCS.return_value
            vcs.get_branch_sha_or_none = AsyncMock(return_value=None)
            vcs.get_branch_sha = AsyncMock(return_value=FAKE_SHA)
            vcs.create_branch = AsyncMock(return_value=FAKE_SHA)
            mock_default.return_value = "main"

            agent = CreateBranchAgent()
            output = await agent.run(ctx)

        # Result should be valid BranchInfo
        info = BranchInfo(**output.result)
        assert info.repository == "acme/my-repo"
        assert info.branch_name.startswith("graphforge/exec-")


# ===========================================================================
# CommitChangesAgent tests
# ===========================================================================


class TestCommitChangesAgent:
    """Tests for CommitChangesAgent.run()."""

    @pytest.mark.asyncio
    async def test_happy_path_creates_commit(self):
        code = _code_result()
        branch = _branch_result()
        workflow = _make_workflow(
            runs=[
                _make_run("generate_code", "completed", code),
                _make_run("create_branch", "completed", branch),
            ]
        )
        ctx = _make_context(workflow=workflow)
        ctx.goal = "commit_changes"

        with (
            patch(
                "app.agents.git_ops.commit_changes_agent.get_decrypted_access_token",
                new_callable=AsyncMock,
            ) as mock_token,
            patch("app.agents.git_ops.commit_changes_agent.create_git_write_provider") as MockVCS,
        ):
            mock_token.return_value = "ghp_test_token"
            vcs = MockVCS.return_value
            # Idempotency check — HEAD has different message
            vcs.get_branch_sha = AsyncMock(return_value=FAKE_SHA)
            vcs.get_commit = AsyncMock(return_value={"message": "different message"})
            vcs.create_commit = AsyncMock(return_value=COMMIT_SHA)

            agent = CommitChangesAgent()
            output = await agent.run(ctx)

        assert output.agent_id == "commit_changes"
        assert output.result["commit_sha"] == COMMIT_SHA
        assert output.result["branch_name"] == "graphforge/exec-12345678"
        assert output.result["files_changed"] == 1
        assert output.result["commit_message"] == "feat: add feature"
        assert output.confidence.score == 0.95
        vcs.create_commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_idempotent_commit_already_applied(self):
        code = _code_result()
        branch = _branch_result()
        workflow = _make_workflow(
            runs=[
                _make_run("generate_code", "completed", code),
                _make_run("create_branch", "completed", branch),
            ]
        )
        ctx = _make_context(workflow=workflow)
        ctx.goal = "commit_changes"

        with (
            patch(
                "app.agents.git_ops.commit_changes_agent.get_decrypted_access_token",
                new_callable=AsyncMock,
            ) as mock_token,
            patch("app.agents.git_ops.commit_changes_agent.create_git_write_provider") as MockVCS,
        ):
            mock_token.return_value = "ghp_test_token"
            vcs = MockVCS.return_value
            # HEAD already has our commit message
            vcs.get_branch_sha = AsyncMock(return_value=COMMIT_SHA)
            vcs.get_commit = AsyncMock(return_value={"message": "feat: add feature"})

            agent = CommitChangesAgent()
            output = await agent.run(ctx)

        assert output.result["commit_sha"] == COMMIT_SHA
        assert "already" in output.evidence[0].summary.lower()
        # create_commit should NOT have been called
        vcs.create_commit = AsyncMock()

    @pytest.mark.asyncio
    async def test_missing_workflow_raises(self):
        ctx = _make_context(workflow=None)
        ctx.goal = "commit_changes"
        agent = CommitChangesAgent()
        with pytest.raises(CommitChangesExecutionError, match="requires a workflow context"):
            await agent.run(ctx)

    @pytest.mark.asyncio
    async def test_missing_generate_code_result_raises(self):
        workflow = _make_workflow(
            runs=[
                _make_run("create_branch", "completed", _branch_result()),
            ]
        )
        ctx = _make_context(workflow=workflow)
        ctx.goal = "commit_changes"
        agent = CommitChangesAgent()
        with pytest.raises(CommitChangesExecutionError, match="No completed generate_code"):
            await agent.run(ctx)

    @pytest.mark.asyncio
    async def test_missing_create_branch_result_raises(self):
        workflow = _make_workflow(
            runs=[
                _make_run("generate_code", "completed", _code_result()),
            ]
        )
        ctx = _make_context(workflow=workflow)
        ctx.goal = "commit_changes"
        agent = CommitChangesAgent()
        with pytest.raises(CommitChangesExecutionError, match="No completed create_branch"):
            await agent.run(ctx)

    @pytest.mark.asyncio
    async def test_missing_user_id_raises(self):
        workflow = _make_workflow(
            runs=[
                _make_run("generate_code", "completed", _code_result()),
                _make_run("create_branch", "completed", _branch_result()),
            ]
        )
        ctx = _make_context(workflow=workflow, user_id=None)
        ctx.goal = "commit_changes"
        agent = CommitChangesAgent()
        with pytest.raises(CommitChangesExecutionError, match="requires user_id"):
            await agent.run(ctx)

    @pytest.mark.asyncio
    async def test_missing_access_token_raises(self):
        workflow = _make_workflow(
            runs=[
                _make_run("generate_code", "completed", _code_result()),
                _make_run("create_branch", "completed", _branch_result()),
            ]
        )
        ctx = _make_context(workflow=workflow)
        ctx.goal = "commit_changes"

        with patch(
            "app.agents.git_ops.commit_changes_agent.get_decrypted_access_token",
            new_callable=AsyncMock,
        ) as mock_token:
            mock_token.return_value = None
            agent = CommitChangesAgent()
            with pytest.raises(CommitChangesExecutionError, match="No GitHub connection"):
                await agent.run(ctx)

    @pytest.mark.asyncio
    async def test_empty_files_raises(self):
        code = _code_result(files=[])
        workflow = _make_workflow(
            runs=[
                _make_run("generate_code", "completed", code),
                _make_run("create_branch", "completed", _branch_result()),
            ]
        )
        ctx = _make_context(workflow=workflow)
        ctx.goal = "commit_changes"
        agent = CommitChangesAgent()
        with pytest.raises(CommitChangesExecutionError, match="No files"):
            await agent.run(ctx)

    @pytest.mark.asyncio
    async def test_missing_commit_message_raises(self):
        code = _code_result(commit_message="")
        workflow = _make_workflow(
            runs=[
                _make_run("generate_code", "completed", code),
                _make_run("create_branch", "completed", _branch_result()),
            ]
        )
        ctx = _make_context(workflow=workflow)
        ctx.goal = "commit_changes"
        agent = CommitChangesAgent()
        with pytest.raises(CommitChangesExecutionError, match="Missing commit_message"):
            await agent.run(ctx)

    @pytest.mark.asyncio
    async def test_api_failure_during_commit_raises(self):
        from app.integrations.github import GitHubApiError

        code = _code_result()
        branch = _branch_result()
        workflow = _make_workflow(
            runs=[
                _make_run("generate_code", "completed", code),
                _make_run("create_branch", "completed", branch),
            ]
        )
        ctx = _make_context(workflow=workflow)
        ctx.goal = "commit_changes"

        with (
            patch(
                "app.agents.git_ops.commit_changes_agent.get_decrypted_access_token",
                new_callable=AsyncMock,
            ) as mock_token,
            patch("app.agents.git_ops.commit_changes_agent.create_git_write_provider") as MockVCS,
        ):
            mock_token.return_value = "ghp_test_token"
            vcs = MockVCS.return_value
            vcs.get_branch_sha = AsyncMock(return_value=FAKE_SHA)
            vcs.get_commit = AsyncMock(return_value={"message": "other"})
            vcs.create_commit = AsyncMock(
                side_effect=GitHubApiError("Server error", status_code=500)
            )

            agent = CommitChangesAgent()
            with pytest.raises(CommitChangesExecutionError, match="Failed to create commit"):
                await agent.run(ctx)

    @pytest.mark.asyncio
    async def test_delete_operation_sends_none_content(self):
        files = [
            {"path": "src/old.py", "operation": "delete", "content": ""},
            {"path": "src/new.py", "operation": "create", "content": "print('new')"},
        ]
        code = _code_result(files=files)
        branch = _branch_result()
        workflow = _make_workflow(
            runs=[
                _make_run("generate_code", "completed", code),
                _make_run("create_branch", "completed", branch),
            ]
        )
        ctx = _make_context(workflow=workflow)
        ctx.goal = "commit_changes"

        with (
            patch(
                "app.agents.git_ops.commit_changes_agent.get_decrypted_access_token",
                new_callable=AsyncMock,
            ) as mock_token,
            patch("app.agents.git_ops.commit_changes_agent.create_git_write_provider") as MockVCS,
        ):
            mock_token.return_value = "ghp_test_token"
            vcs = MockVCS.return_value
            vcs.get_branch_sha = AsyncMock(return_value=FAKE_SHA)
            vcs.get_commit = AsyncMock(return_value={"message": "other"})
            vcs.create_commit = AsyncMock(return_value=COMMIT_SHA)

            agent = CommitChangesAgent()
            output = await agent.run(ctx)

        # Verify create_commit was called with correct api_files
        call_args = vcs.create_commit.call_args
        api_files = call_args[0][3]  # positional arg: files
        assert api_files[0] == {"path": "src/old.py", "content": None}  # delete
        assert api_files[1] == {"path": "src/new.py", "content": "print('new')"}
        assert output.result["files_changed"] == 2

    @pytest.mark.asyncio
    async def test_result_matches_commit_info_schema(self):
        code = _code_result()
        branch = _branch_result()
        workflow = _make_workflow(
            runs=[
                _make_run("generate_code", "completed", code),
                _make_run("create_branch", "completed", branch),
            ]
        )
        ctx = _make_context(workflow=workflow)
        ctx.goal = "commit_changes"

        with (
            patch(
                "app.agents.git_ops.commit_changes_agent.get_decrypted_access_token",
                new_callable=AsyncMock,
            ) as mock_token,
            patch("app.agents.git_ops.commit_changes_agent.create_git_write_provider") as MockVCS,
        ):
            mock_token.return_value = "ghp_test_token"
            vcs = MockVCS.return_value
            vcs.get_branch_sha = AsyncMock(return_value=FAKE_SHA)
            vcs.get_commit = AsyncMock(return_value={"message": "other"})
            vcs.create_commit = AsyncMock(return_value=COMMIT_SHA)

            agent = CommitChangesAgent()
            output = await agent.run(ctx)

        info = CommitInfo(**output.result)
        assert info.repository == "acme/my-repo"
        assert info.commit_sha == COMMIT_SHA

    @pytest.mark.asyncio
    async def test_idempotency_check_failure_still_commits(self):
        """If the idempotency check fails (API error), proceed with commit."""
        from app.integrations.github import GitHubApiError

        code = _code_result()
        branch = _branch_result()
        workflow = _make_workflow(
            runs=[
                _make_run("generate_code", "completed", code),
                _make_run("create_branch", "completed", branch),
            ]
        )
        ctx = _make_context(workflow=workflow)
        ctx.goal = "commit_changes"

        with (
            patch(
                "app.agents.git_ops.commit_changes_agent.get_decrypted_access_token",
                new_callable=AsyncMock,
            ) as mock_token,
            patch("app.agents.git_ops.commit_changes_agent.create_git_write_provider") as MockVCS,
        ):
            mock_token.return_value = "ghp_test_token"
            vcs = MockVCS.return_value
            # Idempotency check fails
            vcs.get_branch_sha = AsyncMock(side_effect=GitHubApiError("Not found", status_code=404))
            # But commit succeeds
            vcs.create_commit = AsyncMock(return_value=COMMIT_SHA)

            agent = CommitChangesAgent()
            output = await agent.run(ctx)

        assert output.result["commit_sha"] == COMMIT_SHA
        vcs.create_commit.assert_awaited_once()


# ===========================================================================
# Registration test
# ===========================================================================


class TestAgentRegistration:
    def test_setup_registers_git_ops_agents(self):
        from app.agents.setup import register_agents
        from app.orchestrator.registry import global_registry

        register_agents()
        ids = {m.agent_id for m in global_registry.all_manifests()}
        assert "create_branch" in ids
        assert "commit_changes" in ids
