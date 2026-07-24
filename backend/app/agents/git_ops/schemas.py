"""Strongly-typed artifacts for the git operations agents.

BranchInfo  — output of create_branch
CommitInfo  — output of commit_changes
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
