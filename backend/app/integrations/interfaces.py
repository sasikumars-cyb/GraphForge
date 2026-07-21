"""Contracts for external systems, implemented by nothing yet.

Defining these now - before any adapter exists - is what lets a GitHub
client and a Jira client be added later as pure additions to this package,
with no change to any service that depends on the interface.
"""

from abc import ABC, abstractmethod
from typing import Any


class IVersionControlProvider(ABC):
    """Port for fetching repository content and change diffs.

    Future implementation: a GitHub adapter (not built yet).
    """

    @abstractmethod
    async def get_diff(self, repository: str, ref: str) -> Any:
        """Return the diff for `ref` within `repository`."""
        raise NotImplementedError


class IIssueTrackerProvider(ABC):
    """Port for reading and creating tracker issues.

    Future implementation: a Jira adapter (not built yet).
    """

    @abstractmethod
    async def create_issue(self, project_key: str, summary: str, description: str) -> Any:
        """Create an issue and return its identifier."""
        raise NotImplementedError
