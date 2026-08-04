"""KAN-35 — `ReadGitDiffTool` (app.ai.agent.tools), specifically its
truncation contract.

`IVersionControlProvider.get_diff` and the tool wrapping it were already
fully implemented and wired into the investigation agent's tool loop
before this ticket (real GitHub `.diff`-media-type fetch in
`GitHubVersionControlProvider.get_diff`; called only for non-trivial-risk
PRs, per `ReadGitDiffTool`'s own docstring) - `docs/architecture/
overview.md` incorrectly described `get_diff` as unimplemented, corrected
in the same change as this test. What had no dedicated test of its own
was the truncation boundary itself (`_MAX_DIFF_CHARS`), covered only
incidentally through `test_investigation_agent.py`'s end-to-end runs.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.ai.agent.models import AgentState
from app.ai.agent.tools import _MAX_DIFF_CHARS, ReadGitDiffTool
from app.integrations.interfaces import IVersionControlProvider

pytestmark = pytest.mark.asyncio


def _tool(provider: IVersionControlProvider) -> ReadGitDiffTool:
    return ReadGitDiffTool(
        version_control_provider=provider,
        owner="acme",
        repo="widgets",
        pull_number=42,
        access_token="ghp_test_token",
    )


async def test_diff_under_budget_is_not_truncated() -> None:
    diff_text = "a" * (_MAX_DIFF_CHARS - 1)
    provider = AsyncMock(spec=IVersionControlProvider)
    provider.get_diff.return_value = diff_text
    state = AgentState()

    observation = await _tool(provider).execute(state)

    assert state.diff_content == diff_text
    assert observation.data["truncated"] is False
    assert observation.data["diff_chars"] == len(diff_text)
    assert "truncated" not in observation.summary


async def test_diff_over_budget_is_truncated_with_a_visible_marker() -> None:
    diff_text = "a" * (_MAX_DIFF_CHARS + 500)
    provider = AsyncMock(spec=IVersionControlProvider)
    provider.get_diff.return_value = diff_text
    state = AgentState()

    observation = await _tool(provider).execute(state)

    assert len(state.diff_content) == _MAX_DIFF_CHARS + len(
        "\n... (diff truncated for prompt budget) ..."
    )
    assert state.diff_content.startswith("a" * _MAX_DIFF_CHARS)
    assert state.diff_content.endswith("... (diff truncated for prompt budget) ...")
    assert observation.data["truncated"] is True
    assert "truncated" in observation.summary


async def test_diff_at_exactly_the_budget_is_not_truncated() -> None:
    """Boundary case: `len(diff) > _MAX_DIFF_CHARS` is a strict
    inequality, so a diff of exactly `_MAX_DIFF_CHARS` characters must
    pass through unmodified, not be treated as one character over."""
    diff_text = "a" * _MAX_DIFF_CHARS
    provider = AsyncMock(spec=IVersionControlProvider)
    provider.get_diff.return_value = diff_text
    state = AgentState()

    observation = await _tool(provider).execute(state)

    assert state.diff_content == diff_text
    assert observation.data["truncated"] is False


async def test_execute_calls_get_diff_with_the_constructor_arguments() -> None:
    provider = AsyncMock(spec=IVersionControlProvider)
    provider.get_diff.return_value = "diff --git a/x b/x"
    state = AgentState()

    await _tool(provider).execute(state)

    provider.get_diff.assert_awaited_once_with(
        owner="acme", repo="widgets", pull_number=42, access_token="ghp_test_token"
    )
