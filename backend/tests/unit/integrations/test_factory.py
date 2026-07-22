"""Unit tests for `create_version_control_provider`."""

import pytest

from app.core.config import Settings
from app.integrations.factory import (
    UnsupportedVersionControlProviderError,
    create_version_control_provider,
)
from app.integrations.github import GitHubVersionControlProvider
from app.integrations.local_git import LocalGitVersionControlProvider


def test_defaults_to_github() -> None:
    settings = Settings()
    provider = create_version_control_provider(settings)
    assert isinstance(provider, GitHubVersionControlProvider)


def test_explicit_github() -> None:
    settings = Settings(vcs_provider="github")
    provider = create_version_control_provider(settings)
    assert isinstance(provider, GitHubVersionControlProvider)


def test_local_git() -> None:
    settings = Settings(vcs_provider="local_git", demo_repositories_root="/tmp/demo-repos")
    provider = create_version_control_provider(settings)
    assert isinstance(provider, LocalGitVersionControlProvider)


def test_unknown_provider_raises() -> None:
    settings = Settings(vcs_provider="bitbucket")
    with pytest.raises(UnsupportedVersionControlProviderError):
        create_version_control_provider(settings)
