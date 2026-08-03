"""Unit tests for `PromptBuilder.run` — mocks `invoke_llm_json` (already
covered by `app.agents.llm`'s own tests) to verify JSON-fence stripping,
success/failure evidence shaping, and graceful degradation."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.agents._contract import AgentContext, Subject
from app.agents.frontier.prompt_builder import PromptSpec, run
from app.core.exceptions import AppError

pytestmark = pytest.mark.asyncio


def _context() -> AgentContext:
    return AgentContext(
        subject=Subject(subject_id="repo:x", subject_type="repository"),
        goal="analyze_repository_understanding",
        extras={"db": object()},
    )


def _spec() -> PromptSpec:
    return PromptSpec(system_prompt="sys", user_prompt="user", stage="repository_understanding")


async def test_run_parses_bare_json_response() -> None:
    with patch(
        "app.agents.frontier.prompt_builder.invoke_llm_json",
        new=AsyncMock(return_value='{"summary": "ok"}'),
    ):
        parsed, evidence = await run(_context(), _spec())

    assert parsed == {"summary": "ok"}
    assert evidence.status == "success"
    assert evidence.kind == "llm_reasoning"


async def test_run_strips_json_fence() -> None:
    with patch(
        "app.agents.frontier.prompt_builder.invoke_llm_json",
        new=AsyncMock(return_value='```json\n{"summary": "ok"}\n```'),
    ):
        parsed, evidence = await run(_context(), _spec())

    assert parsed == {"summary": "ok"}
    assert evidence.status == "success"


async def test_run_degrades_gracefully_on_provider_failure() -> None:
    with patch(
        "app.agents.frontier.prompt_builder.invoke_llm_json",
        new=AsyncMock(side_effect=AppError("provider unavailable")),
    ):
        parsed, evidence = await run(_context(), _spec())

    assert parsed == {}
    assert evidence.status == "failed"
    assert "provider unavailable" in evidence.summary


async def test_run_degrades_gracefully_on_malformed_json() -> None:
    with patch(
        "app.agents.frontier.prompt_builder.invoke_llm_json",
        new=AsyncMock(return_value="not json"),
    ):
        parsed, evidence = await run(_context(), _spec())

    assert parsed == {}
    assert evidence.status == "failed"


async def test_run_degrades_gracefully_on_non_object_json() -> None:
    with patch(
        "app.agents.frontier.prompt_builder.invoke_llm_json",
        new=AsyncMock(return_value="[1, 2, 3]"),
    ):
        parsed, evidence = await run(_context(), _spec())

    assert parsed == {}
    assert evidence.status == "failed"


async def test_run_passes_stage_and_model_through() -> None:
    context = _context()
    context.model = "claude-sonnet-5"
    mock_invoke = AsyncMock(return_value="{}")
    with patch("app.agents.frontier.prompt_builder.invoke_llm_json", new=mock_invoke):
        await run(context, _spec())

    _, kwargs = mock_invoke.call_args
    assert kwargs["stage"] == "repository_understanding"
    assert kwargs["model"] == "claude-sonnet-5"
    assert kwargs["context"] is context
