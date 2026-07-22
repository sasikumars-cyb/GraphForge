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
from app.integrations.interfaces import IVersionControlProvider
from app.integrations.local_git import LocalGitVersionControlProvider


class UnsupportedVersionControlProviderError(AppError):
    """Raised when `vcs_provider` names a provider that doesn't exist."""

    status_code = 501
    error_code = "unsupported_vcs_provider"


def create_version_control_provider(settings: Settings | None = None) -> IVersionControlProvider:
    cfg = settings or get_settings()
    provider_name = cfg.vcs_provider.lower()

    if provider_name == "github":
        return GitHubVersionControlProvider()

    if provider_name == "local_git":
        return LocalGitVersionControlProvider(clone_root=Path(cfg.demo_repositories_root))

    raise UnsupportedVersionControlProviderError(f"Unknown VCS provider: '{cfg.vcs_provider}'.")
