"""`PromptBuilder` — the one place a Frontier agent calls the LLM. Wraps
`app.agents.llm.invoke_llm_json` (unmodified) rather than calling
`StageAwareLLMProvider` directly, so every Frontier agent gets the same
ADR-0012 invocation-metadata persistence `documentation_health` and every
other agent already get, without re-deriving it.

Prompts summarize already-computed service results; nothing here queries
a database or traverses a graph (`build_prompt`, the subclass hook this
feeds, only ever receives an already-materialized `ExecutionResult`).

On any failure (provider error, malformed JSON) this degrades to an empty
narrative plus a `status="failed"` `Evidence` entry rather than raising —
the same graceful-degradation contract `documentation_health._synthesize`
established: a report's deterministic facts must never depend on the
model succeeding.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from app.agents._contract import AgentContext, Evidence
from app.agents.llm import invoke_llm_json
from app.core.exceptions import AppError


@dataclass(frozen=True)
class PromptSpec:
    system_prompt: str
    user_prompt: str
    stage: str
    purpose: str = "initial"
    sequence: int = 0


def _strip_json_fence(text: str) -> str:
    """Strip a ```json ... ``` fence if present — the same normalization
    `app.agents.documentation_health.agent._strip_json_fence` applies,
    consolidated here so every Frontier agent shares one implementation
    instead of each copying it (documentation_health's own docstring notes
    Bedrock/Haiku wraps JSON responses in a fence often enough in practice
    that `ResponseFormat.JSON` alone isn't sufficient)."""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    without_open = stripped.split("\n", 1)[1] if "\n" in stripped else ""
    return without_open.rsplit("```", 1)[0].strip()


class PromptBuilderError(AppError):
    """Generic provider-failure remapping for Frontier agents that don't
    need a more specific `AppError` subclass — `invoke_llm_json` requires
    one; most Frontier agents have no reason to define their own."""


async def run(
    context: AgentContext, spec: PromptSpec, *, error_cls: type[AppError] = PromptBuilderError
) -> tuple[dict[str, object], Evidence]:
    try:
        text = await invoke_llm_json(
            system_prompt=spec.system_prompt,
            user_prompt=spec.user_prompt,
            stage=spec.stage,
            model=context.model,
            error_cls=error_cls,
            context=context,
            purpose=spec.purpose,
            sequence=spec.sequence,
        )
        parsed = json.loads(_strip_json_fence(text))
        if not isinstance(parsed, dict):
            raise ValueError(f"Expected a JSON object, got {type(parsed).__name__}")
        return parsed, Evidence(
            kind="llm_reasoning",
            reference="prompt_builder:invoke_llm_json",
            summary="Generated narrative sections from the computed service results.",
            status="success",
        )
    except (AppError, json.JSONDecodeError, ValueError) as exc:
        return {}, Evidence(
            kind="llm_reasoning",
            reference="prompt_builder:invoke_llm_json",
            summary=f"Narrative could not be generated ({exc}); computed results are unaffected.",
            status="failed",
        )
