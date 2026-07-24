"""Strongly-typed artifacts for the git operations agents.

BranchInfo      — output of create_branch
CommitInfo      — output of commit_changes
TestRunInfo     — output of run_tests
PullRequestInfo — output of create_pull_request
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class BranchInfo(BaseModel):
    """Result of the create_branch stage."""

    goal: str = "create_branch"
    executive_summary: str = ""
    repository: str  # "owner/repo"
    branch_name: str
    base_sha: str


class CommitInfo(BaseModel):
    """Result of the commit_changes stage."""

    goal: str = "commit_changes"
    executive_summary: str = ""
    repository: str
    branch_name: str
    commit_sha: str
    files_changed: int
    commit_message: str


class TestRunInfo(BaseModel):
    """Result of the run_tests stage (CI observation)."""

    goal: str = "run_tests"
    executive_summary: str = ""
    repository: str
    branch_name: str
    commit_sha: str
    workflow_name: str = ""
    run_id: int | None = None
    status: str  # "success" | "failed" | "in_progress" | "queued" | "timeout" | "unknown"
    conclusion: str = ""  # GitHub conclusion: "success" | "failure" | "timed_out" | etc.
    html_url: str = ""
    started_at: str = ""
    completed_at: str = ""


class PullRequestInfo(BaseModel):
    """Result of the create_pull_request stage.

    `pull_request_id` is the internal `pull_requests.id` (not GitHub's own
    PR id/number) — it's what the workflow router passes to the existing
    Review Agent as `pr:<pull_request_id>` for the ai_pr_review stage."""

    goal: str = "create_pull_request"
    executive_summary: str = ""
    pull_request_id: str
    github_pr_number: int
    html_url: str
    repository: str
    branch: str
    base_branch: str
    title: str
    body: str
    state: str
    created_at: str
