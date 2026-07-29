"""Factory for creating `IVersionControlProvider` instances.

Mirrors `app.ai.providers.factory.create_llm_provider`'s pattern: reads a
setting, returns the matching concrete implementation. Defaults to GitHub,
so existing/production call sites are unaffected unless `VCS_PROVIDER` is
explicitly set to opt into the local demo environment.
"""

from __future__ import annotations

from pathlib import Path

from app.core.config import Settings, get_settings
from app.core.exceptions import AppError
from app.integrations.github import GitHubVersionControlProvider
from app.integrations.interfaces import IGitWriteProvider, IVersionControlProvider
from app.integrations.local_git import LocalGitVersionControlProvider


class UnsupportedVersionControlProviderError(AppError):
    """Raised when `vcs_provider` names a provider that doesn't exist."""

    status_code = 501
    error_code = "unsupported_vcs_provider"


class VersionControlWritesUnsupportedError(AppError):
    """Raised when the configured `vcs_provider` has no `IGitWriteProvider`
    implementation — e.g. `local_git`, a deliberately read-only demo
    backend for PR impact analysis (see LocalGitVersionControlProvider's
    docstring). Execution workflows (create_branch, commit_changes,
    create_pull_request, run_tests) need a real write-capable backend;
    failing loudly and early here is safer than a confusing downstream
    AttributeError from calling a method that was never implemented."""

    status_code = 501
    error_code = "vcs_writes_unsupported"


def create_version_control_provider(settings: Settings | None = None) -> IVersionControlProvider:
    cfg = settings or get_settings()
    provider_name = cfg.vcs_provider.lower()

    if provider_name == "github":
        return GitHubVersionControlProvider()

    if provider_name == "local_git":
        return LocalGitVersionControlProvider(clone_root=Path(cfg.demo_repositories_root))

    raise UnsupportedVersionControlProviderError(f"Unknown VCS provider: '{cfg.vcs_provider}'.")


def create_git_write_provider(settings: Settings | None = None) -> IGitWriteProvider:
    """Resolve the configured `vcs_provider`'s write-capable backend.

    Execution agents (create_branch, commit_changes, create_pull_request,
    run_tests) and PR-comment-posting call sites use this instead of
    constructing `GitHubVersionControlProvider` directly — the one seam a
    future GitLab/Bitbucket/Azure DevOps `IGitWriteProvider` adapter would
    need to be reached through, with no change to any of those agents.
    """
    cfg = settings or get_settings()
    provider_name = cfg.vcs_provider.lower()

    if provider_name == "github":
        return GitHubVersionControlProvider()

    raise VersionControlWritesUnsupportedError(
        f"VCS provider '{cfg.vcs_provider}' does not support git write operations "
        "(branch/commit/pull-request creation). Configure 'github' (or a future "
        "write-capable provider) to run execution workflows."
    )
