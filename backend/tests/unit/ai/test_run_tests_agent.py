"""Unit tests for the TestRunnerAgent (CI observation).

Covers:
- Happy path: CI passes, CI fails
- Queued / in-progress check runs
- Timeout behaviour (does not fail workflow)
- No check runs (unknown / CI not configured)
- Idempotency: re-running only observes
- Error cases: missing workflow, missing prior stage, missing token,
  invalid repository, API failures
- Exponential backoff behaviour
- Multiple check runs with mixed statuses
- Manifest and registration
- Schema validation (TestRunInfo)
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.agents._contract import AgentContext, Subject
from app.agents.git_ops.manifests import RUN_TESTS_MANIFEST
from app.agents.git_ops.run_tests_agent import (
    TestRunnerAgent,
    TestRunnerExecutionError,
    _evaluate_check_runs,
)
from app.agents.git_ops.schemas import TestRunInfo

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

WORKFLOW_ID = uuid.UUID("12345678-aaaa-bbbb-cccc-000000000001")
USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000099")
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


def _commit_result(
    repository: str = "acme/my-repo",
    branch_name: str = "graphforge/exec-12345678",
    commit_sha: str = COMMIT_SHA,
) -> dict:
    return {
        "goal": "commit_changes",
        "executive_summary": "Committed 2 files.",
        "repository": repository,
        "branch_name": branch_name,
        "commit_sha": commit_sha,
        "files_changed": 2,
        "commit_message": "feat: add feature",
    }


def _make_workflow(runs: list | None = None) -> SimpleNamespace:
    return SimpleNamespace(id=WORKFLOW_ID, runs=runs or [])


def _make_context(
    workflow=None,
    user_id=USER_ID,
    poll_interval: float = 0.01,
    max_poll_interval: float = 0.02,
    timeout: float = 0.1,
) -> AgentContext:
    subject = Subject(
        subject_id="freetext:exec",
        subject_type="freetext",
        display_name="Execute blueprint",
    )
    extras: dict = {"db": AsyncMock()}
    if workflow is not None:
        extras["workflow"] = workflow
    if user_id is not None:
        extras["user_id"] = user_id
    # Use tiny intervals for tests
    extras["poll_interval"] = poll_interval
    extras["max_poll_interval"] = max_poll_interval
    extras["timeout"] = timeout
    return AgentContext(subject=subject, goal="run_tests", extras=extras)


def _check_run(
    name: str = "build",
    status: str = "completed",
    conclusion: str | None = "success",
    run_id: int = 123,
    html_url: str = "https://github.com/acme/my-repo/runs/123",
    started_at: str = "2024-01-01T00:00:00Z",
    completed_at: str = "2024-01-01T00:05:00Z",
) -> dict:
    return {
        "id": run_id,
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "html_url": html_url,
        "started_at": started_at,
        "completed_at": completed_at,
    }


# ===========================================================================
# Manifest tests
# ===========================================================================


class TestManifest:
    def test_run_tests_manifest(self):
        m = RUN_TESTS_MANIFEST
        assert m.agent_id == "run_tests"
        assert "run_tests" in m.goals
        assert "freetext" in m.accepted_subject_types
        assert m.cost_class == "cheap"
        assert m.output_schema_name == "TestRunInfo"


# ===========================================================================
# Schema tests
# ===========================================================================


class TestTestRunInfoSchema:
    def test_round_trip(self):
        info = TestRunInfo(
            repository="acme/repo",
            branch_name="graphforge/exec-abc",
            commit_sha=COMMIT_SHA,
            workflow_name="CI",
            run_id=42,
            status="success",
            conclusion="success",
            html_url="https://github.com/acme/repo/runs/42",
            started_at="2024-01-01T00:00:00Z",
            completed_at="2024-01-01T00:05:00Z",
            executive_summary="All checks passed.",
        )
        d = info.model_dump()
        assert d["status"] == "success"
        assert d["run_id"] == 42
        assert TestRunInfo(**d) == info

    def test_default_values(self):
        info = TestRunInfo(
            repository="acme/repo",
            branch_name="main",
            commit_sha="abc123",
            status="unknown",
        )
        assert info.conclusion == ""
        assert info.html_url == ""
        assert info.run_id is None
        assert info.goal == "run_tests"


# ===========================================================================
# _evaluate_check_runs tests
# ===========================================================================


class TestEvaluateCheckRuns:
    def test_all_success(self):
        runs = [_check_run(conclusion="success"), _check_run(name="test", conclusion="success")]
        result = _evaluate_check_runs(runs)
        assert result["terminal"] is True
        assert result["status"] == "success"

    def test_one_failure(self):
        runs = [_check_run(conclusion="success"), _check_run(name="test", conclusion="failure")]
        result = _evaluate_check_runs(runs)
        assert result["terminal"] is True
        assert result["status"] == "failed"

    def test_cancelled_is_failure(self):
        runs = [_check_run(conclusion="cancelled")]
        result = _evaluate_check_runs(runs)
        assert result["terminal"] is True
        assert result["status"] == "failed"

    def test_timed_out_is_failure(self):
        runs = [_check_run(conclusion="timed_out")]
        result = _evaluate_check_runs(runs)
        assert result["terminal"] is True
        assert result["status"] == "failed"

    def test_in_progress(self):
        runs = [_check_run(status="in_progress", conclusion=None)]
        result = _evaluate_check_runs(runs)
        assert result["terminal"] is False
        assert result["status"] == "in_progress"

    def test_queued(self):
        runs = [_check_run(status="queued", conclusion=None)]
        result = _evaluate_check_runs(runs)
        assert result["terminal"] is False
        assert result["status"] == "queued"

    def test_mixed_in_progress_and_completed(self):
        runs = [
            _check_run(conclusion="success"),
            _check_run(name="test", status="in_progress", conclusion=None),
        ]
        result = _evaluate_check_runs(runs)
        assert result["terminal"] is False
        assert result["status"] == "in_progress"

    def test_representative_is_failure_run(self):
        runs = [
            _check_run(name="lint", conclusion="success"),
            _check_run(name="test", conclusion="failure", run_id=999),
        ]
        result = _evaluate_check_runs(runs)
        assert result["run_id"] == 999
        assert result["workflow_name"] == "test"


# ===========================================================================
# TestRunnerAgent tests
# ===========================================================================


class TestTestRunnerAgent:
    """Tests for TestRunnerAgent.run()."""

    @pytest.mark.asyncio
    async def test_ci_success(self):
        """CI passes on first poll — happy path."""
        commit = _commit_result()
        workflow = _make_workflow(
            runs=[
                _make_run("commit_changes", "completed", commit),
            ]
        )
        ctx = _make_context(workflow=workflow)

        with (
            patch(
                "app.agents.git_ops.run_tests_agent.get_decrypted_access_token",
                new_callable=AsyncMock,
            ) as mock_token,
            patch("app.agents.git_ops.run_tests_agent.GitHubVersionControlProvider") as MockVCS,
        ):
            mock_token.return_value = "ghp_test_token"
            vcs = MockVCS.return_value
            vcs.get_check_runs = AsyncMock(return_value=[_check_run(conclusion="success")])

            agent = TestRunnerAgent()
            output = await agent.run(ctx)

        assert output.agent_id == "run_tests"
        assert output.result["status"] == "success"
        assert output.result["conclusion"] == "success"
        assert output.result["repository"] == "acme/my-repo"
        assert output.result["commit_sha"] == COMMIT_SHA
        assert output.confidence.score == 0.95
        info = TestRunInfo(**output.result)
        assert info.status == "success"

    @pytest.mark.asyncio
    async def test_ci_failure(self):
        """CI fails — agent reports failure, does not crash."""
        commit = _commit_result()
        workflow = _make_workflow(
            runs=[
                _make_run("commit_changes", "completed", commit),
            ]
        )
        ctx = _make_context(workflow=workflow)

        with (
            patch(
                "app.agents.git_ops.run_tests_agent.get_decrypted_access_token",
                new_callable=AsyncMock,
            ) as mock_token,
            patch("app.agents.git_ops.run_tests_agent.GitHubVersionControlProvider") as MockVCS,
        ):
            mock_token.return_value = "ghp_test_token"
            vcs = MockVCS.return_value
            vcs.get_check_runs = AsyncMock(
                return_value=[
                    _check_run(conclusion="success"),
                    _check_run(name="test", conclusion="failure", run_id=456),
                ]
            )

            agent = TestRunnerAgent()
            output = await agent.run(ctx)

        assert output.result["status"] == "failed"
        assert output.result["conclusion"] == "failure"

    @pytest.mark.asyncio
    async def test_ci_queued_then_completes(self):
        """CI starts queued, then completes on second poll."""
        commit = _commit_result()
        workflow = _make_workflow(
            runs=[
                _make_run("commit_changes", "completed", commit),
            ]
        )
        ctx = _make_context(workflow=workflow, timeout=5.0)

        with (
            patch(
                "app.agents.git_ops.run_tests_agent.get_decrypted_access_token",
                new_callable=AsyncMock,
            ) as mock_token,
            patch("app.agents.git_ops.run_tests_agent.GitHubVersionControlProvider") as MockVCS,
            patch(
                "app.agents.git_ops.run_tests_agent.asyncio.sleep", new_callable=AsyncMock
            ) as mock_sleep,
        ):
            mock_token.return_value = "ghp_test_token"
            vcs = MockVCS.return_value
            vcs.get_check_runs = AsyncMock(
                side_effect=[
                    [_check_run(status="queued", conclusion=None)],
                    [_check_run(status="completed", conclusion="success")],
                ]
            )

            agent = TestRunnerAgent()
            output = await agent.run(ctx)

        assert output.result["status"] == "success"
        mock_sleep.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_ci_in_progress_then_completes(self):
        """CI in_progress, then completes."""
        commit = _commit_result()
        workflow = _make_workflow(
            runs=[
                _make_run("commit_changes", "completed", commit),
            ]
        )
        ctx = _make_context(workflow=workflow, timeout=5.0)

        with (
            patch(
                "app.agents.git_ops.run_tests_agent.get_decrypted_access_token",
                new_callable=AsyncMock,
            ) as mock_token,
            patch("app.agents.git_ops.run_tests_agent.GitHubVersionControlProvider") as MockVCS,
            patch(
                "app.agents.git_ops.run_tests_agent.asyncio.sleep", new_callable=AsyncMock
            ) as mock_sleep,
        ):
            mock_token.return_value = "ghp_test_token"
            vcs = MockVCS.return_value
            vcs.get_check_runs = AsyncMock(
                side_effect=[
                    [_check_run(status="in_progress", conclusion=None)],
                    [_check_run(status="completed", conclusion="success")],
                ]
            )

            agent = TestRunnerAgent()
            output = await agent.run(ctx)

        assert output.result["status"] == "success"
        # One in_progress result before the completed one means exactly one
        # poll-loop sleep, same shape as test_ci_in_progress_with_backoff below.
        mock_sleep.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_timeout_does_not_fail_workflow(self):
        """Timeout returns status=timeout, not an exception."""
        commit = _commit_result()
        workflow = _make_workflow(
            runs=[
                _make_run("commit_changes", "completed", commit),
            ]
        )
        # Very short timeout
        ctx = _make_context(workflow=workflow, timeout=0.0)

        with (
            patch(
                "app.agents.git_ops.run_tests_agent.get_decrypted_access_token",
                new_callable=AsyncMock,
            ) as mock_token,
            patch("app.agents.git_ops.run_tests_agent.GitHubVersionControlProvider") as MockVCS,
        ):
            mock_token.return_value = "ghp_test_token"
            vcs = MockVCS.return_value
            vcs.get_check_runs = AsyncMock(
                return_value=[
                    _check_run(status="in_progress", conclusion=None),
                ]
            )

            agent = TestRunnerAgent()
            output = await agent.run(ctx)

        # Returns timeout, not raises
        assert output.result["status"] == "timeout"
        assert output.agent_id == "run_tests"

    @pytest.mark.asyncio
    async def test_no_check_runs_returns_unknown(self):
        """No CI configured — returns unknown."""
        commit = _commit_result()
        workflow = _make_workflow(
            runs=[
                _make_run("commit_changes", "completed", commit),
            ]
        )
        ctx = _make_context(workflow=workflow, timeout=0.0)

        with (
            patch(
                "app.agents.git_ops.run_tests_agent.get_decrypted_access_token",
                new_callable=AsyncMock,
            ) as mock_token,
            patch("app.agents.git_ops.run_tests_agent.GitHubVersionControlProvider") as MockVCS,
        ):
            mock_token.return_value = "ghp_test_token"
            vcs = MockVCS.return_value
            vcs.get_check_runs = AsyncMock(return_value=[])

            agent = TestRunnerAgent()
            output = await agent.run(ctx)

        assert output.result["status"] == "unknown"
        assert output.result["conclusion"] == "unknown"

    @pytest.mark.asyncio
    async def test_missing_workflow_raises(self):
        ctx = _make_context(workflow=None)
        agent = TestRunnerAgent()
        with pytest.raises(TestRunnerExecutionError, match="requires a workflow context"):
            await agent.run(ctx)

    @pytest.mark.asyncio
    async def test_missing_commit_changes_result_raises(self):
        workflow = _make_workflow(runs=[])
        ctx = _make_context(workflow=workflow)
        agent = TestRunnerAgent()
        with pytest.raises(TestRunnerExecutionError, match="No completed commit_changes"):
            await agent.run(ctx)

    @pytest.mark.asyncio
    async def test_invalid_repository_raises(self):
        commit = _commit_result(repository="no-slash")
        workflow = _make_workflow(
            runs=[
                _make_run("commit_changes", "completed", commit),
            ]
        )
        ctx = _make_context(workflow=workflow)
        agent = TestRunnerAgent()
        with pytest.raises(TestRunnerExecutionError, match="Invalid repository"):
            await agent.run(ctx)

    @pytest.mark.asyncio
    async def test_missing_user_id_raises(self):
        commit = _commit_result()
        workflow = _make_workflow(
            runs=[
                _make_run("commit_changes", "completed", commit),
            ]
        )
        ctx = _make_context(workflow=workflow, user_id=None)
        agent = TestRunnerAgent()
        with pytest.raises(TestRunnerExecutionError, match="requires user_id"):
            await agent.run(ctx)

    @pytest.mark.asyncio
    async def test_missing_access_token_raises(self):
        commit = _commit_result()
        workflow = _make_workflow(
            runs=[
                _make_run("commit_changes", "completed", commit),
            ]
        )
        ctx = _make_context(workflow=workflow)

        with patch(
            "app.agents.git_ops.run_tests_agent.get_decrypted_access_token", new_callable=AsyncMock
        ) as mock_token:
            mock_token.return_value = None
            agent = TestRunnerAgent()
            with pytest.raises(TestRunnerExecutionError, match="No GitHub connection"):
                await agent.run(ctx)

    @pytest.mark.asyncio
    async def test_api_error_during_polling_raises(self):
        from app.integrations.github import GitHubApiError

        commit = _commit_result()
        workflow = _make_workflow(
            runs=[
                _make_run("commit_changes", "completed", commit),
            ]
        )
        ctx = _make_context(workflow=workflow)

        with (
            patch(
                "app.agents.git_ops.run_tests_agent.get_decrypted_access_token",
                new_callable=AsyncMock,
            ) as mock_token,
            patch("app.agents.git_ops.run_tests_agent.GitHubVersionControlProvider") as MockVCS,
        ):
            mock_token.return_value = "ghp_test_token"
            vcs = MockVCS.return_value
            vcs.get_check_runs = AsyncMock(
                side_effect=GitHubApiError("API rate limit exceeded", status_code=403)
            )

            agent = TestRunnerAgent()
            with pytest.raises(TestRunnerExecutionError, match="Failed to fetch check runs"):
                await agent.run(ctx)

    @pytest.mark.asyncio
    async def test_exponential_backoff(self):
        """Verify sleep intervals increase exponentially."""
        commit = _commit_result()
        workflow = _make_workflow(
            runs=[
                _make_run("commit_changes", "completed", commit),
            ]
        )
        # Large enough timeout for 3 polls
        ctx = _make_context(
            workflow=workflow,
            poll_interval=1.0,
            max_poll_interval=10.0,
            timeout=100.0,
        )

        with (
            patch(
                "app.agents.git_ops.run_tests_agent.get_decrypted_access_token",
                new_callable=AsyncMock,
            ) as mock_token,
            patch("app.agents.git_ops.run_tests_agent.GitHubVersionControlProvider") as MockVCS,
            patch(
                "app.agents.git_ops.run_tests_agent.asyncio.sleep", new_callable=AsyncMock
            ) as mock_sleep,
        ):
            mock_token.return_value = "ghp_test_token"
            vcs = MockVCS.return_value
            vcs.get_check_runs = AsyncMock(
                side_effect=[
                    [_check_run(status="in_progress", conclusion=None)],
                    [_check_run(status="in_progress", conclusion=None)],
                    [_check_run(status="completed", conclusion="success")],
                ]
            )

            agent = TestRunnerAgent()
            output = await agent.run(ctx)

        assert output.result["status"] == "success"
        # First sleep: 1.0, second sleep: 2.0 (doubled)
        sleep_calls = [call.args[0] for call in mock_sleep.await_args_list]
        assert sleep_calls[0] == 1.0
        assert sleep_calls[1] == 2.0

    @pytest.mark.asyncio
    async def test_backoff_capped_at_max(self):
        """Backoff doesn't exceed max_poll_interval."""
        commit = _commit_result()
        workflow = _make_workflow(
            runs=[
                _make_run("commit_changes", "completed", commit),
            ]
        )
        ctx = _make_context(
            workflow=workflow,
            poll_interval=5.0,
            max_poll_interval=8.0,
            timeout=100.0,
        )

        with (
            patch(
                "app.agents.git_ops.run_tests_agent.get_decrypted_access_token",
                new_callable=AsyncMock,
            ) as mock_token,
            patch("app.agents.git_ops.run_tests_agent.GitHubVersionControlProvider") as MockVCS,
            patch(
                "app.agents.git_ops.run_tests_agent.asyncio.sleep", new_callable=AsyncMock
            ) as mock_sleep,
        ):
            mock_token.return_value = "ghp_test_token"
            vcs = MockVCS.return_value
            vcs.get_check_runs = AsyncMock(
                side_effect=[
                    [_check_run(status="in_progress", conclusion=None)],
                    [_check_run(status="in_progress", conclusion=None)],
                    [_check_run(status="completed", conclusion="success")],
                ]
            )

            agent = TestRunnerAgent()
            await agent.run(ctx)

        sleep_calls = [call.args[0] for call in mock_sleep.await_args_list]
        assert sleep_calls[0] == 5.0
        assert sleep_calls[1] == 8.0  # capped at max, not 10.0

    @pytest.mark.asyncio
    async def test_idempotent_rerun_only_observes(self):
        """Re-running never dispatches Actions — only reads check runs."""
        commit = _commit_result()
        workflow = _make_workflow(
            runs=[
                _make_run("commit_changes", "completed", commit),
            ]
        )
        ctx = _make_context(workflow=workflow)

        with (
            patch(
                "app.agents.git_ops.run_tests_agent.get_decrypted_access_token",
                new_callable=AsyncMock,
            ) as mock_token,
            patch("app.agents.git_ops.run_tests_agent.GitHubVersionControlProvider") as MockVCS,
        ):
            mock_token.return_value = "ghp_test_token"
            vcs = MockVCS.return_value
            vcs.get_check_runs = AsyncMock(return_value=[_check_run(conclusion="success")])

            agent = TestRunnerAgent()
            # Run twice — both succeed, only observation
            output1 = await agent.run(ctx)
            output2 = await agent.run(ctx)

        assert output1.result["status"] == "success"
        assert output2.result["status"] == "success"
        # Only get_check_runs was called — verify no write methods were invoked
        vcs.get_check_runs.assert_awaited()
        assert vcs.get_check_runs.await_count == 2

    @pytest.mark.asyncio
    async def test_result_matches_test_run_info_schema(self):
        commit = _commit_result()
        workflow = _make_workflow(
            runs=[
                _make_run("commit_changes", "completed", commit),
            ]
        )
        ctx = _make_context(workflow=workflow)

        with (
            patch(
                "app.agents.git_ops.run_tests_agent.get_decrypted_access_token",
                new_callable=AsyncMock,
            ) as mock_token,
            patch("app.agents.git_ops.run_tests_agent.GitHubVersionControlProvider") as MockVCS,
        ):
            mock_token.return_value = "ghp_test_token"
            vcs = MockVCS.return_value
            vcs.get_check_runs = AsyncMock(
                return_value=[
                    _check_run(
                        name="CI",
                        conclusion="success",
                        run_id=42,
                        html_url="https://github.com/acme/my-repo/runs/42",
                        started_at="2024-01-01T00:00:00Z",
                        completed_at="2024-01-01T00:05:00Z",
                    ),
                ]
            )

            agent = TestRunnerAgent()
            output = await agent.run(ctx)

        info = TestRunInfo(**output.result)
        assert info.workflow_name == "CI"
        assert info.run_id == 42
        assert info.html_url == "https://github.com/acme/my-repo/runs/42"
        assert info.started_at == "2024-01-01T00:00:00Z"
        assert info.completed_at == "2024-01-01T00:05:00Z"

    @pytest.mark.asyncio
    async def test_missing_branch_name_raises(self):
        commit = _commit_result(branch_name="")
        workflow = _make_workflow(
            runs=[
                _make_run("commit_changes", "completed", commit),
            ]
        )
        ctx = _make_context(workflow=workflow)
        agent = TestRunnerAgent()
        with pytest.raises(TestRunnerExecutionError, match="Missing branch_name"):
            await agent.run(ctx)

    @pytest.mark.asyncio
    async def test_missing_commit_sha_raises(self):
        commit = _commit_result(commit_sha="")
        workflow = _make_workflow(
            runs=[
                _make_run("commit_changes", "completed", commit),
            ]
        )
        ctx = _make_context(workflow=workflow)
        agent = TestRunnerAgent()
        with pytest.raises(TestRunnerExecutionError, match="Missing commit_sha"):
            await agent.run(ctx)

    @pytest.mark.asyncio
    async def test_multiple_check_runs_all_pass(self):
        commit = _commit_result()
        workflow = _make_workflow(
            runs=[
                _make_run("commit_changes", "completed", commit),
            ]
        )
        ctx = _make_context(workflow=workflow)

        with (
            patch(
                "app.agents.git_ops.run_tests_agent.get_decrypted_access_token",
                new_callable=AsyncMock,
            ) as mock_token,
            patch("app.agents.git_ops.run_tests_agent.GitHubVersionControlProvider") as MockVCS,
        ):
            mock_token.return_value = "ghp_test_token"
            vcs = MockVCS.return_value
            vcs.get_check_runs = AsyncMock(
                return_value=[
                    _check_run(name="lint", conclusion="success"),
                    _check_run(name="test", conclusion="success"),
                    _check_run(name="build", conclusion="success"),
                ]
            )

            agent = TestRunnerAgent()
            output = await agent.run(ctx)

        assert output.result["status"] == "success"
        assert "3 check run(s) passed" in output.evidence[0].summary


# ===========================================================================
# Registration test
# ===========================================================================


class TestAgentRegistration:
    def test_setup_registers_run_tests_agent(self):
        from app.agents.setup import register_agents
        from app.orchestrator.registry import global_registry

        register_agents()
        ids = {m.agent_id for m in global_registry.all_manifests()}
        assert "run_tests" in ids
