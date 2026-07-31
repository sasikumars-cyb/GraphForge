"""Generic bounded reflection-retry.

The Planning Agent runs one critique-and-refine pass: a deterministic
(non-LLM) gap check on the first draft, and — only when it finds a real
structural gap — a single bounded second LLM call asking the model to fix
exactly those gaps. This is one of three distinct retry shapes this
codebase uses (see the LLM architecture inventory in the accompanying
deliverable):

  1. Provider-level fallback retry — `app.ai.config.fallback.
     complete_with_fallback`, already shared by every stage via
     `StageAwareLLMProvider`/`invoke_llm_json`. Retries *across vendors*
     on a recoverable transport failure (rate limit, timeout, 5xx).
  2. Reflection retry — this module. Retries the *same* call once, on a
     deterministic content-quality gap, never on a transport failure.
  3. Confidence-triggered retry — `InvestigationAgent.
     should_retry_after_low_confidence` (`app/ai/agent/investigation_agent.py`).
     Deliberately left untouched: `app/ai/agent/*` is frozen by
     `review_adapter.py`'s own design (see that module's docstring), and
     its retry is a single hard-capped tool re-execution, not a second LLM
     call — a different shape than either of the above.

Only Planning uses this module today; Development, Testing, Documentation
Planning, Engineering Review, and Code Generation each run a single pass
(see the inventory — none of them had a reflection loop to begin with, so
extracting this doesn't change their behavior). Giving the *existing*
Planning behavior a shared, reusable, generically-typed home means a
future stage can adopt the same bounded-cost, deterministic-trigger shape
by supplying its own gap-finder/parser/prompt-builder, instead of
re-deriving the token-summing and best-effort-failure bookkeeping from
scratch the way Planning originally did inline.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


@dataclass
class ReflectionOutcome(Generic[T]):
    """What a `run_with_reflection` call decided. `applied=False` means
    the original draft won — either no gap was found, the refine call
    failed, or the refine draft still had gaps — so callers always get a
    valid `result` back regardless of outcome."""

    prompt: str
    raw_response: str
    result: T
    metadata: dict[str, Any]
    applied: bool
    gaps: list[str] = field(default_factory=list)


async def run_with_reflection(
    *,
    initial_prompt: str,
    initial_raw: str,
    initial_result: T,
    initial_metadata: dict[str, Any],
    find_gaps: Callable[[T], list[str]],
    parse: Callable[[str], T],
    call_llm: Callable[[str, dict[str, Any]], Awaitable[str]],
    build_refine_prompt: Callable[[str, str, list[str]], str],
    recoverable_error: type[Exception],
    max_trace_chars: int = 20_000,
) -> ReflectionOutcome[T]:
    """One bounded critique-and-refine pass, at most once per call.

    `find_gaps` MUST be deterministic (no LLM call) — this never spends a
    second LLM call just to *judge* the first draft; a refine call only
    fires when `find_gaps(initial_result)` finds something concrete, so
    cost stays bounded regardless of how many runs pass through this.

    `call_llm(refine_prompt, metadata_out)` is expected to raise
    `recoverable_error` on provider failure — that failure is swallowed
    here (logged as a warning) and the original, still-valid result is
    kept: reflection is a best-effort quality pass, never a hard
    dependency for the run to succeed.

    Token usage from both calls is summed into `initial_metadata` (the
    caller's own out-param, mutated in place) regardless of which draft
    ultimately wins, since both cost real money.
    """
    gaps = find_gaps(initial_result)
    if not gaps:
        return ReflectionOutcome(
            prompt=initial_prompt,
            raw_response=initial_raw,
            result=initial_result,
            metadata=initial_metadata,
            applied=False,
            gaps=[],
        )

    logger.info("reflection_triggered gaps=%s", gaps)
    refine_prompt = build_refine_prompt(initial_prompt, initial_raw[:max_trace_chars], gaps)

    try:
        refined_metadata: dict[str, Any] = {}
        refined_raw = await call_llm(refine_prompt, refined_metadata)
        refined_result = parse(refined_raw)

        # Both calls cost real money and real time regardless of which draft
        # wins — sum the additive metrics across both rather than reporting
        # only one of them, so the metadata reflects what this run actually
        # spent. `latency_ms`/`estimated_cost_usd`/`retry_count` are summed
        # for the same reason token counts always were: reporting only the
        # first call's latency alongside both calls' tokens would be
        # internally inconsistent. Non-additive fields (`status`,
        # `finish_reason`, timestamps, provider/model) are handled below —
        # for those the *final* call is the meaningful one.
        for additive in (
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "latency_ms",
            "estimated_cost_usd",
            "retry_count",
        ):
            if refined_metadata.get(additive) is not None:
                initial_metadata[additive] = (
                    initial_metadata.get(additive) or 0
                ) + refined_metadata[additive]

        if find_gaps(refined_result):
            logger.info("reflection_did_not_resolve_gaps")
            return ReflectionOutcome(
                prompt=initial_prompt,
                raw_response=initial_raw,
                result=initial_result,
                metadata=initial_metadata,
                applied=False,
                gaps=gaps,
            )

        # Non-additive fields: the refined call is the one whose result is
        # being returned, so its provider/model/outcome are the meaningful
        # ones to report. `started_at` deliberately keeps the *first* call's
        # value so the pair spans the whole reflection window.
        for last_wins in ("provider", "model", "finish_reason", "status", "finished_at"):
            if refined_metadata.get(last_wins) is not None:
                initial_metadata[last_wins] = refined_metadata[last_wins]
        return ReflectionOutcome(
            prompt=refine_prompt,
            raw_response=refined_raw,
            result=refined_result,
            metadata=initial_metadata,
            applied=True,
            gaps=gaps,
        )
    except recoverable_error:
        logger.warning("reflection_call_failed", exc_info=True)
        return ReflectionOutcome(
            prompt=initial_prompt,
            raw_response=initial_raw,
            result=initial_result,
            metadata=initial_metadata,
            applied=False,
            gaps=gaps,
        )
