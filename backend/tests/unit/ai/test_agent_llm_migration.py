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


@pytest.mark.parametrize(
    "module_path,call_llm,error_cls,stage", _CASES, ids=[c[0] for c in _CASES]
)
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


@pytest.mark.parametrize(
    "module_path,call_llm,error_cls,stage", _CASES, ids=[c[0] for c in _CASES]
)
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


@pytest.mark.parametrize(
    "module_path,call_llm,error_cls,stage", _CASES, ids=[c[0] for c in _CASES]
)
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
