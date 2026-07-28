"""TestRunner Agent — observes repository CI status for a commit.

Deterministic execution: no LLM, no prompt, no AI reasoning.
Polls the GitHub Check Runs API with exponential backoff until all
check runs reach a terminal state or the configurable timeout expires.

This agent ONLY observes. It never dispatches GitHub Actions, never
creates workflows, never triggers CI. Re-running is always safe —
it simply re-reads the current check-run state.

Possible result statuses:
  success     — all check runs completed successfully
  failed      — at least one check run failed
  in_progress — at least one check run is still running (returned on timeout)
  queued      — all check runs are queued (returned on timeout)
  timeout     — polling timeout expired before CI reached a terminal state
  unknown     — no check runs found (CI may not be configured)
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from app.agents._contract import (
    AgentContext,
    AgentOutput,
    Confidence,
    Evidence,
)
from app.agents.git_ops._artifact_reader import get_stage_result
from app.agents.git_ops.schemas import TestRunInfo
from app.core.exceptions import AppError
from app.integrations.github import GitHubApiError, GitHubVersionControlProvider
from app.services.github_service import get_decrypted_access_token

logger = logging.getLogger(__name__)

# Configurable defaults — overridable via context.extras for testing
DEFAULT_POLL_INTERVAL = 10  # seconds (initial)
DEFAULT_MAX_POLL_INTERVAL = 60  # seconds (cap after backoff)
DEFAULT_TIMEOUT = 600  # seconds (10 minutes)

# GitHub Check Run terminal statuses
_TERMINAL_STATUSES = frozenset({"completed"})


class TestRunnerExecutionError(AppError):
    status_code = 502
    error_code = "run_tests_execution_error"


class TestRunnerAgent:
    """Implements IAgent for goal=run_tests. Stateless singleton."""

    async def run(self, context: AgentContext) -> AgentOutput:
        db = context.extras["db"]
        workflow = context.extras.get("workflow")
        subject_id = context.subject.subject_id

        if workflow is None:
            raise TestRunnerExecutionError("run_tests requires a workflow context.")

        # --- Read prior stage results ---
        commit_result = get_stage_result(workflow, "commit_changes")
        if commit_result is None:
            raise TestRunnerExecutionError("No completed commit_changes result found in workflow.")

        repository = commit_result.get("repository", "")
        if not repository or "/" not in repository:
            raise TestRunnerExecutionError(
                f"Invalid repository in commit_changes result: '{repository}'"
            )

        owner, repo = repository.split("/", 1)
        branch_name = commit_result.get("branch_name", "")
        commit_sha = commit_result.get("commit_sha", "")

        if not branch_name:
            raise TestRunnerExecutionError("Missing branch_name in commit_changes result.")
        if not commit_sha:
            raise TestRunnerExecutionError("Missing commit_sha in commit_changes result.")

        # --- Get access token ---
        user_id = context.extras.get("user_id")
        if user_id is None:
            raise TestRunnerExecutionError("run_tests requires user_id in context extras.")
        access_token = await get_decrypted_access_token(db, user_id)
        if access_token is None:
            raise TestRunnerExecutionError(
                "No GitHub connection found. Connect GitHub before running execution workflows."
            )

        # --- Polling configuration ---
        poll_interval = context.extras.get("poll_interval", DEFAULT_POLL_INTERVAL)
        max_poll_interval = context.extras.get("max_poll_interval", DEFAULT_MAX_POLL_INTERVAL)
        timeout = context.extras.get("timeout", DEFAULT_TIMEOUT)

        vcs = GitHubVersionControlProvider()
        evidence: list[Evidence] = []

        # --- Poll loop ---
        start_time = time.monotonic()
        current_interval = poll_interval
        last_check_runs: list[dict[str, Any]] = []
        poll_count = 0

        while True:
            elapsed = time.monotonic() - start_time

            try:
                check_runs = await vcs.get_check_runs(owner, repo, commit_sha, access_token)
                last_check_runs = check_runs
                poll_count += 1
            except GitHubApiError as exc:
                raise TestRunnerExecutionError(
                    f"Failed to fetch check runs for {commit_sha[:8]}: {exc}"
                ) from exc

            # No check runs at all — CI not configured or not triggered yet
            if not check_runs:
                # On first poll, give CI a moment to start; on timeout, report unknown
                if elapsed >= timeout:
                    evidence.append(
                        Evidence(
                            kind="tool_call",
                            reference="github_check_runs",
                            summary=(
                                f"No check runs found for commit {commit_sha[:8]} "
                                f"after {int(elapsed)}s ({poll_count} polls). "
                                f"CI may not be configured."
                            ),
                        )
                    )
                    return _build_output(
                        subject_id=subject_id,
                        repository=repository,
                        branch_name=branch_name,
                        commit_sha=commit_sha,
                        status="unknown",
                        conclusion="unknown",
                        evidence=evidence,
                        summary=f"No CI check runs found for commit {commit_sha[:8]}.",
                    )
            else:
                # Evaluate aggregate status
                result = _evaluate_check_runs(check_runs)

                if result["terminal"]:
                    evidence.append(
                        Evidence(
                            kind="tool_call",
                            reference="github_check_runs",
                            summary=(
                                f"CI completed after {int(elapsed)}s ({poll_count} polls): "
                                f"{result['status']} — {result['summary']}"
                            ),
                        )
                    )
                    return _build_output(
                        subject_id=subject_id,
                        repository=repository,
                        branch_name=branch_name,
                        commit_sha=commit_sha,
                        status=result["status"],
                        conclusion=result["conclusion"],
                        evidence=evidence,
                        summary=result["summary"],
                        workflow_name=result.get("workflow_name", ""),
                        run_id=result.get("run_id"),
                        html_url=result.get("html_url", ""),
                        started_at=result.get("started_at", ""),
                        completed_at=result.get("completed_at", ""),
                    )

            # Check timeout BEFORE sleeping
            if elapsed + current_interval >= timeout:
                # One more check won't fit — report timeout with last known state
                status = "timeout"
                conclusion = "timeout"
                summary_text = (
                    f"CI observation timed out after {int(elapsed)}s " f"({poll_count} polls)."
                )

                if last_check_runs:
                    last_eval = _evaluate_check_runs(last_check_runs)
                    status = last_eval["status"]
                    conclusion = last_eval["conclusion"]
                    summary_text = (
                        f"CI observation timed out after {int(elapsed)}s — "
                        f"last status: {status} ({last_eval['summary']})"
                    )

                evidence.append(
                    Evidence(
                        kind="tool_call",
                        reference="github_check_runs",
                        summary=summary_text,
                    )
                )
                return _build_output(
                    subject_id=subject_id,
                    repository=repository,
                    branch_name=branch_name,
                    commit_sha=commit_sha,
                    status="timeout",
                    conclusion=conclusion,
                    evidence=evidence,
                    summary=summary_text,
                )

            # --- Exponential backoff ---
            logger.debug(
                "run_tests_poll repo=%s sha=%s poll=%d interval=%.0fs",
                repository,
                commit_sha[:8],
                poll_count,
                current_interval,
            )
            await asyncio.sleep(current_interval)
            current_interval = min(current_interval * 2, max_poll_interval)


def _evaluate_check_runs(check_runs: list[dict[str, Any]]) -> dict[str, Any]:
    """Evaluate the aggregate status of a list of GitHub check runs.

    Returns a dict with:
      terminal   — bool: all runs have reached a terminal state
      status     — str: "success" | "failed" | "in_progress" | "queued"
      conclusion — str: GitHub conclusion or aggregate
      summary    — str: human-readable summary
      workflow_name, run_id, html_url, started_at, completed_at — from
        the most relevant check run (first failure, or first run)
    """
    all_completed = all(cr.get("status") in _TERMINAL_STATUSES for cr in check_runs)

    # Collect conclusions for completed runs
    conclusions = [cr.get("conclusion", "") for cr in check_runs if cr.get("status") == "completed"]
    in_progress = [cr for cr in check_runs if cr.get("status") == "in_progress"]
    queued = [cr for cr in check_runs if cr.get("status") == "queued"]

    # Pick the most relevant check run for detail fields
    # Priority: first failure > first in-progress > first completed > first queued
    failed_runs = [
        cr for cr in check_runs if cr.get("conclusion") in ("failure", "cancelled", "timed_out")
    ]
    representative = (
        failed_runs[0] if failed_runs else in_progress[0] if in_progress else check_runs[0]
    )

    detail = {
        "workflow_name": representative.get("name", ""),
        "run_id": representative.get("id"),
        "html_url": representative.get("html_url", ""),
        "started_at": representative.get("started_at", ""),
        "completed_at": representative.get("completed_at", ""),
    }

    if all_completed:
        has_failure = any(c in ("failure", "cancelled", "timed_out") for c in conclusions)
        if has_failure:
            return {
                "terminal": True,
                "status": "failed",
                "conclusion": representative.get("conclusion", "failure"),
                "summary": (f"{len(failed_runs)} of {len(check_runs)} check run(s) failed."),
                **detail,
            }
        else:
            return {
                "terminal": True,
                "status": "success",
                "conclusion": "success",
                "summary": f"All {len(check_runs)} check run(s) passed.",
                **detail,
            }

    # Not all completed yet
    if in_progress:
        return {
            "terminal": False,
            "status": "in_progress",
            "conclusion": "",
            "summary": (f"{len(in_progress)} of {len(check_runs)} check run(s) still running."),
            **detail,
        }

    return {
        "terminal": False,
        "status": "queued",
        "conclusion": "",
        "summary": f"{len(queued)} of {len(check_runs)} check run(s) queued.",
        **detail,
    }


def _build_output(
    *,
    subject_id: str,
    repository: str,
    branch_name: str,
    commit_sha: str,
    status: str,
    conclusion: str,
    evidence: list[Evidence],
    summary: str,
    workflow_name: str = "",
    run_id: int | None = None,
    html_url: str = "",
    started_at: str = "",
    completed_at: str = "",
) -> AgentOutput:
    result = TestRunInfo(
        repository=repository,
        branch_name=branch_name,
        commit_sha=commit_sha,
        workflow_name=workflow_name,
        run_id=run_id,
        status=status,
        conclusion=conclusion,
        html_url=html_url,
        started_at=started_at,
        completed_at=completed_at,
        executive_summary=summary,
    )

    return AgentOutput(
        agent_id="run_tests",
        subject_id=subject_id,
        confidence=Confidence(
            score=0.95,
            reasoning="Deterministic CI observation via GitHub Check Runs API.",
        ),
        evidence=evidence,
        result=result.model_dump(),
    )
