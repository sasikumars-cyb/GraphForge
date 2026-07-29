"""Unit tests for the shared LLM execution infrastructure introduced to
de-duplicate per-agent invocation, reflection-retry, and confidence code:

- app.agents.llm.invoke_llm_json      (shared single-shot JSON invocation)
- app.agents.reflection.run_with_reflection  (shared bounded reflection retry)
- app.agents.confidence.calculate_weighted_confidence (shared evidence engine)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.confidence import WeightedEvidence, calculate_weighted_confidence
from app.agents.llm import invoke_llm_json
from app.agents.reflection import run_with_reflection
from app.ai.providers.base import LLMResponse
from app.core.exceptions import AppError

# ---------------------------------------------------------------------------
# invoke_llm_json
# ---------------------------------------------------------------------------


class _BoomError(AppError):
    status_code = 502
    error_code = "boom"


@pytest.mark.asyncio
async def test_invoke_llm_json_returns_text_and_resolves_stage() -> None:
    mock_provider = MagicMock()
    mock_provider.last_resolved = None
    mock_provider.complete = AsyncMock(return_value=LLMResponse(text='{"ok": true}'))
    mock_factory = MagicMock(return_value=mock_provider)

    with patch("app.agents.llm.StageAwareLLMProvider", mock_factory):
        result = await invoke_llm_json(
            system_prompt="sys",
            user_prompt="some prompt",
            stage="planning",
            model="gpt-5",
            error_cls=_BoomError,
        )

    assert result == '{"ok": true}'
    mock_factory.assert_called_once_with(stage="planning", model="gpt-5")


@pytest.mark.asyncio
async def test_invoke_llm_json_remaps_provider_error() -> None:
    provider_error = AppError("Rate limited.")
    provider_error.provider_error = {"provider": "openai"}  # type: ignore[attr-defined]
    mock_provider = MagicMock()
    mock_provider.complete = AsyncMock(side_effect=provider_error)
    mock_factory = MagicMock(return_value=mock_provider)

    with (
        patch("app.agents.llm.StageAwareLLMProvider", mock_factory),
        pytest.raises(_BoomError, match="Rate limited."),
    ):
        await invoke_llm_json(
            system_prompt="sys",
            user_prompt="p",
            stage="planning",
            model=None,
            error_cls=_BoomError,
        )


@pytest.mark.asyncio
async def test_invoke_llm_json_populates_metadata_out() -> None:
    resolved = MagicMock(key="openai", model="gpt-5")
    mock_provider = MagicMock()
    mock_provider.last_resolved = resolved
    mock_provider.complete = AsyncMock(
        return_value=LLMResponse(
            text="{}", model_name="gpt-5", prompt_tokens=10, completion_tokens=5, total_tokens=15
        )
    )
    mock_factory = MagicMock(return_value=mock_provider)
    metadata: dict = {}

    with patch("app.agents.llm.StageAwareLLMProvider", mock_factory):
        await invoke_llm_json(
            system_prompt="sys",
            user_prompt="p",
            stage="planning",
            model=None,
            error_cls=_BoomError,
            metadata_out=metadata,
        )

    assert metadata["provider"] == "openai"
    assert metadata["model"] == "gpt-5"
    assert metadata["total_tokens"] == 15


# ---------------------------------------------------------------------------
# run_with_reflection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reflection_skips_refine_when_no_gaps_found() -> None:
    call_llm = AsyncMock()

    outcome = await run_with_reflection(
        initial_prompt="p",
        initial_raw="raw",
        initial_result={"ok": True},
        initial_metadata={},
        find_gaps=lambda _r: [],
        parse=lambda raw: {"parsed": raw},
        call_llm=call_llm,
        build_refine_prompt=lambda p, r, g: "refine",
        recoverable_error=RuntimeError,
    )

    assert outcome.applied is False
    assert outcome.result == {"ok": True}
    call_llm.assert_not_awaited()


@pytest.mark.asyncio
async def test_reflection_applies_refined_result_when_gaps_resolved() -> None:
    calls = {"n": 0}

    def find_gaps(result: dict) -> list[str]:
        # First draft has a gap; refined draft does not.
        return [] if result.get("refined") else ["missing risks"]

    async def call_llm(prompt: str, metadata_out: dict) -> str:
        calls["n"] += 1
        metadata_out["prompt_tokens"] = 7
        metadata_out["completion_tokens"] = 3
        metadata_out["total_tokens"] = 10
        metadata_out["provider"] = "openai"
        metadata_out["model"] = "gpt-5"
        return "refined-raw"

    metadata = {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7}

    outcome = await run_with_reflection(
        initial_prompt="p",
        initial_raw="raw",
        initial_result={"refined": False},
        initial_metadata=metadata,
        find_gaps=find_gaps,
        parse=lambda raw: {"refined": True},
        call_llm=call_llm,
        build_refine_prompt=lambda p, r, g: f"{p}+refine+{g}",
        recoverable_error=RuntimeError,
    )

    assert calls["n"] == 1
    assert outcome.applied is True
    assert outcome.result == {"refined": True}
    assert outcome.raw_response == "refined-raw"
    # Token usage summed across both calls, not just the winning draft.
    assert metadata["total_tokens"] == 17
    assert metadata["provider"] == "openai"


@pytest.mark.asyncio
async def test_reflection_keeps_original_when_refined_still_has_gaps() -> None:
    async def call_llm(prompt: str, metadata_out: dict) -> str:
        return "still-bad-raw"

    outcome = await run_with_reflection(
        initial_prompt="p",
        initial_raw="raw",
        initial_result={"gap": True},
        initial_metadata={},
        find_gaps=lambda _r: ["still missing risks"],
        parse=lambda raw: {"gap": True},
        call_llm=call_llm,
        build_refine_prompt=lambda p, r, g: "refine",
        recoverable_error=RuntimeError,
    )

    assert outcome.applied is False
    assert outcome.result == {"gap": True}
    assert outcome.raw_response == "raw"  # original, not "still-bad-raw"


@pytest.mark.asyncio
async def test_reflection_keeps_original_when_refine_call_fails() -> None:
    async def call_llm(prompt: str, metadata_out: dict) -> str:
        raise RuntimeError("provider down")

    outcome = await run_with_reflection(
        initial_prompt="p",
        initial_raw="raw",
        initial_result={"gap": True},
        initial_metadata={},
        find_gaps=lambda _r: ["a gap"],
        parse=lambda raw: {"gap": True},
        call_llm=call_llm,
        build_refine_prompt=lambda p, r, g: "refine",
        recoverable_error=RuntimeError,
    )

    assert outcome.applied is False
    assert outcome.result == {"gap": True}


# ---------------------------------------------------------------------------
# calculate_weighted_confidence
# ---------------------------------------------------------------------------


def test_weighted_confidence_full_marks_scores_one() -> None:
    score, reasoning = calculate_weighted_confidence(
        WeightedEvidence(
            flags={"a": True, "b": True},
            weights={"a": 0.6, "b": 0.4},
        )
    )
    assert score == 1.0
    assert "Deterministic confidence" in reasoning
    assert "a=True" in reasoning and "b=True" in reasoning


def test_weighted_confidence_missing_flag_defaults_false() -> None:
    score, _ = calculate_weighted_confidence(
        WeightedEvidence(flags={"a": True}, weights={"a": 0.6, "b": 0.4})
    )
    assert score == 0.6


def test_weighted_confidence_applies_penalty_and_clamps_at_zero() -> None:
    score, reasoning = calculate_weighted_confidence(
        WeightedEvidence(
            flags={"a": True},
            weights={"a": 0.3},
            penalty=0.9,
            penalty_reason="violations=3",
        )
    )
    assert score == 0.0
    assert "violations=3" in reasoning


def test_weighted_confidence_ignores_unweighted_flags() -> None:
    score, _ = calculate_weighted_confidence(
        WeightedEvidence(flags={"a": True, "unrelated": True}, weights={"a": 1.0})
    )
    assert score == 1.0
