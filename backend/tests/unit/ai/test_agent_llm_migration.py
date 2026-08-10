"""Unit tests for the LLM architecture migration.

Every freeform-JSON agent (Planning, Development, Testing, Engineering
Review, Code Generation) delegates its own `_call_llm` to the single
shared `app.agents.llm.invoke_llm_json`, which constructs a
`StageAwareLLMProvider` and calls `Provider.complete()` — resolving
provider+model through the AI configuration layer under the agent's
workflow stage and sending via `app.ai.config.fallback.
complete_with_fallback`. `StageAwareLLMProvider` is therefore constructed
in exactly one place (`app.agents.llm`) rather than once per agent module,
so these tests mock it there — the boundary the seam actually sits at now
— rather than re-testing HTTP status-code mapping, which already has full
coverage in tests/integration/test_openai_provider.py.

The stage assertion below is the regression guard for the defect this
seam was introduced to fix: agents previously called
`create_llm_provider(model=model)` with no stage, so every per-stage
override and AI Profile in the AI Workspace resolved as if unset.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.code_generation.agent import CodeGenerationLLMError
from app.agents.code_generation.agent import _call_llm as call_llm_code_generation
from app.agents.development.agent import DevelopmentLLMError
from app.agents.development.agent import _call_llm as call_llm_development
from app.agents.engineering_review.agent import EngineeringReviewLLMError
from app.agents.engineering_review.agent import _call_llm as call_llm_engineering_review
from app.agents.llm import LLM_INVOCATION_METADATA_KEYS
from app.agents.planning.agent import PlanningLLMError
from app.agents.planning.agent import _call_llm as call_llm_planning
from app.agents.testing.agent import TestingLLMError
from app.agents.testing.agent import _call_llm as call_llm_testing
from app.ai.providers.base import LLMResponse
from app.ai.providers.errors import AIProviderRateLimitError

# (agent module dotted path for patching StageAwareLLMProvider, the agent's
# _call_llm function under test, its own LLM error class, the stage key it
# must resolve AI configuration under by default)
_CASES = [
    ("app.agents.planning.agent", call_llm_planning, PlanningLLMError, "planning"),
    ("app.agents.development.agent", call_llm_development, DevelopmentLLMError, "development"),
    ("app.agents.testing.agent", call_llm_testing, TestingLLMError, "testing"),
    (
        "app.agents.engineering_review.agent",
        call_llm_engineering_review,
        EngineeringReviewLLMError,
        "engineering_review",
    ),
    (
        "app.agents.code_generation.agent",
        call_llm_code_generation,
        CodeGenerationLLMError,
        "generate_code",
    ),
]


@pytest.mark.parametrize("module_path,call_llm,error_cls,stage", _CASES, ids=[c[0] for c in _CASES])
@pytest.mark.asyncio
async def test_call_llm_delegates_to_provider_and_returns_text(
    module_path, call_llm, error_cls, stage
) -> None:
    mock_provider = MagicMock()
    mock_provider.last_resolved = None
    mock_provider.complete = AsyncMock(return_value=LLMResponse(text='{"ok": true}'))
    mock_factory = MagicMock(return_value=mock_provider)

    with patch("app.agents.llm.StageAwareLLMProvider", mock_factory):
        result = await call_llm("some prompt", model="gpt-5")

    assert result == '{"ok": true}'
    # The stage MUST be passed — without it the AI configuration layer
    # cannot apply a per-stage override or a stage-mapped AI Profile.
    mock_factory.assert_called_once_with(stage=stage, model="gpt-5")
    mock_provider.complete.assert_awaited_once()
    _, kwargs = mock_provider.complete.call_args
    assert kwargs["user_prompt"] == "some prompt"
    assert kwargs["options"].response_format.value == "json"


@pytest.mark.parametrize("module_path,call_llm,error_cls,stage", _CASES, ids=[c[0] for c in _CASES])
@pytest.mark.asyncio
async def test_call_llm_remaps_provider_error_to_agent_error(
    module_path, call_llm, error_cls, stage
) -> None:
    provider_error = AIProviderRateLimitError("Rate limit hit for real.")
    provider_error.provider_error = {"provider": "openai", "status_code": 429}

    mock_provider = MagicMock()
    mock_provider.last_resolved = None
    mock_provider.complete = AsyncMock(side_effect=provider_error)
    mock_factory = MagicMock(return_value=mock_provider)

    with (
        patch("app.agents.llm.StageAwareLLMProvider", mock_factory),
        pytest.raises(error_cls) as exc_info,
    ):
        await call_llm("some prompt")

    assert str(exc_info.value) == "Rate limit hit for real."
    assert exc_info.value.provider_error == {"provider": "openai", "status_code": 429}


@pytest.mark.parametrize("module_path,call_llm,error_cls,stage", _CASES, ids=[c[0] for c in _CASES])
@pytest.mark.asyncio
async def test_call_llm_error_without_provider_metadata_defaults_to_none(
    module_path, call_llm, error_cls, stage
) -> None:
    """Resolution itself can raise a bare AppError (e.g. 'not configured')
    with no provider_error attribute at all — must not crash the
    remapping."""
    from app.core.exceptions import AppError

    with (
        patch(
            "app.agents.llm.StageAwareLLMProvider",
            side_effect=AppError("Not configured."),
        ),
        pytest.raises(error_cls) as exc_info,
    ):
        await call_llm("some prompt")

    assert str(exc_info.value) == "Not configured."
    assert exc_info.value.provider_error is None


# ---------------------------------------------------------------------------
# Observability (architecture Weakness #4) — every agent must get the full
# invocation metadata set from the one shared pathway, not collect its own.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("module_path,call_llm,error_cls,stage", _CASES, ids=[c[0] for c in _CASES])
@pytest.mark.asyncio
async def test_every_agent_gets_the_full_invocation_metadata_set(
    module_path, call_llm, error_cls, stage
) -> None:
    """The point of the shared pathway: an agent opts in by passing a dict
    and gets every signal, computed identically. Before this, `metadata_out`
    carried only provider/model/tokens and no caller but Planning used it —
    latency and cost were Planning-only, computed at its own call site."""
    resolved = MagicMock()
    resolved.key = "openai"
    resolved.model = "gpt-4o"

    mock_provider = MagicMock()
    mock_provider.last_resolved = resolved
    mock_provider.last_retry_count = 3
    mock_provider.complete = AsyncMock(
        return_value=LLMResponse(
            text='{"ok": true}',
            model_name="gpt-4o",
            finish_reason="stop",
            prompt_tokens=1_000_000,
            completion_tokens=1_000_000,
            total_tokens=2_000_000,
        )
    )

    metadata: dict[str, object] = {}
    with patch("app.agents.llm.StageAwareLLMProvider", MagicMock(return_value=mock_provider)):
        await call_llm("some prompt", _metadata_out=metadata)

    for key in LLM_INVOCATION_METADATA_KEYS:
        assert key in metadata, f"{key} missing for {module_path}"

    assert metadata["provider"] == "openai"
    assert metadata["model"] == "gpt-4o"
    assert metadata["total_tokens"] == 2_000_000
    assert metadata["finish_reason"] == "stop"
    assert metadata["status"] == "completed"
    assert metadata["error"] is None
    # Retry count comes from the fallback loop, which used to discard it.
    assert metadata["retry_count"] == 3
    assert metadata["latency_ms"] is not None and metadata["latency_ms"] >= 0
    # gpt-4o is in app.ai.providers.pricing: 1M in @ $2.50 + 1M out @ $10.00.
    assert metadata["estimated_cost_usd"] == 12.50


@pytest.mark.parametrize("module_path,call_llm,error_cls,stage", _CASES, ids=[c[0] for c in _CASES])
@pytest.mark.asyncio
async def test_invocation_metadata_is_recorded_on_the_failure_path(
    module_path, call_llm, error_cls, stage
) -> None:
    """A failed invocation is precisely the one worth observing. The agent
    still receives its own error type — the metadata is a side effect."""
    mock_provider = MagicMock()
    mock_provider.last_resolved = None
    mock_provider.last_retry_count = 0
    mock_provider.complete = AsyncMock(side_effect=AIProviderRateLimitError("Rate limited."))

    metadata: dict[str, object] = {}
    with (
        patch("app.agents.llm.StageAwareLLMProvider", MagicMock(return_value=mock_provider)),
        pytest.raises(error_cls),
    ):
        await call_llm("some prompt", _metadata_out=metadata)

    assert metadata["status"] == "failed"
    assert metadata["error"] == "Rate limited."
    assert metadata["latency_ms"] is not None
    # No response, so these are honestly None rather than zero.
    assert metadata["total_tokens"] is None
    assert metadata["estimated_cost_usd"] is None
