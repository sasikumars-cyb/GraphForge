"""Regression tests for the IGitWriteProvider abstraction (Part 4/12):
no workflow instantiates GitHubVersionControlProvider by name, and the
factory fails safely for a backend that doesn't support writes.
"""

from __future__ import annotations

import pytest

from app.core.config import Settings
from app.integrations.factory import (
    VersionControlWritesUnsupportedError,
    create_git_write_provider,
)
from app.integrations.github import GitHubVersionControlProvider
from app.integrations.interfaces import IGitWriteProvider, IVersionControlProvider


def test_github_write_provider_implements_both_interfaces():
    provider = create_git_write_provider(Settings(vcs_provider="github"))
    assert isinstance(provider, IGitWriteProvider)
    assert isinstance(provider, IVersionControlProvider)
    assert isinstance(provider, GitHubVersionControlProvider)


def test_local_git_has_no_write_provider():
    """local_git is a deliberately read-only demo backend — asking the
    factory for a write provider must fail loudly and clearly, not return
    something that crashes on first use."""
    with pytest.raises(VersionControlWritesUnsupportedError):
        create_git_write_provider(Settings(vcs_provider="local_git"))


def test_unknown_provider_raises():
    with pytest.raises(VersionControlWritesUnsupportedError):
        create_git_write_provider(Settings(vcs_provider="not_a_real_provider"))


def test_git_ops_agents_do_not_import_github_provider_by_name():
    """Regression guard for Part 4: none of the execution agents should
    construct GitHubVersionControlProvider directly — they resolve a write
    provider through the factory instead."""
    import ast
    import inspect

    from app.agents.git_ops import (
        commit_changes_agent,
        create_branch_agent,
        create_pull_request_agent,
        run_tests_agent,
    )

    for module in (
        create_branch_agent,
        commit_changes_agent,
        create_pull_request_agent,
        run_tests_agent,
    ):
        source = inspect.getsource(module)
        tree = ast.parse(source)
        constructed_names = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert "GitHubVersionControlProvider" not in constructed_names, module.__name__
        assert "create_git_write_provider" in constructed_names, module.__name__
