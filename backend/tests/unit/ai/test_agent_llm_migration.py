"""Unit tests for the LLM architecture migration.

Every freeform-JSON agent (Planning, Development, Testing, Engineering
Review, Code Generation) now calls `create_llm_provider()` /
`Provider.complete()` directly instead of the removed `app.agents._llm`
duplicate transport. These tests mock at that boundary — exactly the
level the migration moved the seam to — rather than re-testing HTTP
status-code mapping, which already has full coverage in
tests/integration/test_openai_provider.py against the one remaining
transport implementation.
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

# (agent module dotted path for patching create_llm_provider, the agent's
# _call_llm function under test, its own LLM error class)
_CASES = [
    ("app.agents.planning.agent", call_llm_planning, PlanningLLMError),
    ("app.agents.development.agent", call_llm_development, DevelopmentLLMError),
    ("app.agents.testing.agent", call_llm_testing, TestingLLMError),
    ("app.agents.engineering_review.agent", call_llm_engineering_review, EngineeringReviewLLMError),
    ("app.agents.code_generation.agent", call_llm_code_generation, CodeGenerationLLMError),
]


@pytest.mark.parametrize("module_path,call_llm,error_cls", _CASES, ids=[c[0] for c in _CASES])
@pytest.mark.asyncio
async def test_call_llm_delegates_to_provider_and_returns_text(
    module_path, call_llm, error_cls
) -> None:
    mock_provider = MagicMock()
    mock_provider.complete = AsyncMock(return_value=LLMResponse(text='{"ok": true}'))
    mock_factory = MagicMock(return_value=mock_provider)

    with patch(f"{module_path}.create_llm_provider", mock_factory):
        result = await call_llm("some prompt", model="gpt-5")

    assert result == '{"ok": true}'
    mock_factory.assert_called_once_with(model="gpt-5")
    mock_provider.complete.assert_awaited_once()
    _, kwargs = mock_provider.complete.call_args
    assert kwargs["user_prompt"] == "some prompt"
    assert kwargs["options"].response_format.value == "json"


@pytest.mark.parametrize("module_path,call_llm,error_cls", _CASES, ids=[c[0] for c in _CASES])
@pytest.mark.asyncio
async def test_call_llm_remaps_provider_error_to_agent_error(
    module_path, call_llm, error_cls
) -> None:
    provider_error = AIProviderRateLimitError("Rate limit hit for real.")
    provider_error.provider_error = {"provider": "openai", "status_code": 429}

    mock_provider = MagicMock()
    mock_provider.complete = AsyncMock(side_effect=provider_error)
    mock_factory = MagicMock(return_value=mock_provider)

    with (
        patch(f"{module_path}.create_llm_provider", mock_factory),
        pytest.raises(error_cls) as exc_info,
    ):
        await call_llm("some prompt")

    assert str(exc_info.value) == "Rate limit hit for real."
    assert exc_info.value.provider_error == {"provider": "openai", "status_code": 429}


@pytest.mark.parametrize("module_path,call_llm,error_cls", _CASES, ids=[c[0] for c in _CASES])
@pytest.mark.asyncio
async def test_call_llm_error_without_provider_metadata_defaults_to_none(
    module_path, call_llm, error_cls
) -> None:
    """create_llm_provider() itself can raise a bare AppError (e.g.
    'not configured') with no provider_error attribute at all — must not
    crash the remapping."""
    from app.core.exceptions import AppError

    with (
        patch(f"{module_path}.create_llm_provider", side_effect=AppError("Not configured.")),
        pytest.raises(error_cls) as exc_info,
    ):
        await call_llm("some prompt")

    assert str(exc_info.value) == "Not configured."
    assert exc_info.value.provider_error is None
