"""Stage-aware LLM access for agents — the one seam between agent code and
the AI configuration layer.

Why this exists
---------------
Every agent used to call `create_llm_provider(model=model)` directly. That
call *accepts* a `stage` argument, but no caller ever passed one, so
`app.ai.config.resolver.resolve()` always ran with `stage=None` and could
never apply a stage override or a stage-mapped AI Profile. The consequence
was that the AI Workspace's per-stage configuration — persisted, API-served,
and rendered in the UI as the "effective" provider/model for each stage —
had no effect on what actually executed. `app.ai.config.fallback.
complete_with_fallback` (operator-gated, recoverable-error-classified,
cycle-protected) had no call sites at all, while the Planning Agent carried
its own inline three-provider fallback ladder that crossed vendors purely
because env keys happened to be set — the exact behaviour the shared engine
documents as forbidden.

This module closes that gap without introducing a second configuration
system. It is deliberately thin: it owns no precedence rules, no provider
catalogue, and no fallback policy. Those stay in `app.ai.config.resolver`
and `app.ai.config.fallback` respectively. All this does is *ask them the
question with the stage attached*.

Design
------
`StageAwareLLMProvider` implements the existing `ILLMProvider` port, so it
is a drop-in replacement for a `create_llm_provider()` result at every call
site — the six freeform agents that call `complete()` directly, the
Confluence context loop that calls `complete_with_tools()`, and the two
legacy consumers (`InvestigationAgent`, `AIAnalysisService`) that receive a
provider by injection and call `analyze()`. None of those consumers needed
to change shape, and `app/ai/agent/*` stayed untouched, which
`review_adapter.py` explicitly requires.

Per-method fallback support, and why it differs
-----------------------------------------------
`complete()`      -> routed through `complete_with_fallback`. Full stage
                     resolution + profile resolution + operator-configured
                     fallback chain.
`complete_with_tools()` -> stage/profile resolution, **no** fallback. The
                     shared engine wraps single-shot completions; a
                     tool-calling loop carries provider-native message
                     history (see BaseAnalysisProvider.complete_with_tools)
                     that cannot be replayed against a different vendor
                     mid-conversation. This matches today's behaviour
                     exactly — that path never had fallback.
`analyze()`       -> stage/profile resolution, **no** fallback. `analyze()`
                     is the transitional domain-coupled path that builds
                     its own prompt inside the provider, so there is no
                     prompt for the fallback engine to re-send. Also
                     unchanged from today.

In every case the *provider and model selection* now honours stage
configuration, which is the objective. Fallback coverage is strictly a
superset of what existed before, never a reduction.
"""

from __future__ import annotations

import logging
from typing import Any

from app.ai.config.fallback import complete_with_fallback
from app.ai.config.resolver import ResolvedProvider, resolve
from app.ai.interfaces.llm_provider import ILLMProvider
from app.ai.providers.base import (
    LLMRequestOptions,
    LLMResponse,
    ResponseFormat,
    ToolSpec,
    ToolTurnResult,
)
from app.ai.providers.factory import validate_resolution
from app.ai.schemas.analysis_result import AIAnalysisResult
from app.ai.services.context_builder import AIContext
from app.core.exceptions import AppError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Stage keys
# ---------------------------------------------------------------------------
#
# These are the keys the AI Workspace stores stage overrides under, and the
# keys `api/v1/routers/ai_workspace.py` iterates when it reports each
# stage's effective provider/model. They are `workflow_service.STAGE_GOALS`
# keys — NOT agent_ids — because that is what the configuration surface is
# keyed by. The two differ in two places (`code_generation` the agent runs
# as stage `generate_code`; the review agent runs as either `review` or
# `ai_pr_review`), which is exactly why an agent must not simply pass its
# own agent_id here.

STAGE_PLANNING = "planning"
STAGE_DEVELOPMENT = "development"
STAGE_TESTING = "testing"
STAGE_REVIEW = "review"
STAGE_DOCUMENTATION_PLANNING = "documentation_planning"
STAGE_ENGINEERING_REVIEW = "engineering_review"
STAGE_GENERATE_CODE = "generate_code"


# agent_id -> the stage key that agent resolves under when a run carries no
# `workflow_stage` (i.e. a standalone run). Each agent already passes its own
# default to `stage_for()`; this map exists so callers *outside* an agent —
# specifically `POST /agent-runs`, which records `Run.provider` before
# dispatch — can predict the same resolution the agent will perform, instead
# of reporting the raw env provider and being wrong whenever a stage override
# or profile applies.
_AGENT_DEFAULT_STAGE: dict[str, str] = {
    "planning": STAGE_PLANNING,
    "development": STAGE_DEVELOPMENT,
    "testing": STAGE_TESTING,
    "review": STAGE_REVIEW,
    "documentation_planning": STAGE_DOCUMENTATION_PLANNING,
    "engineering_review": STAGE_ENGINEERING_REVIEW,
    "code_generation": STAGE_GENERATE_CODE,
}


def default_stage_for_agent(agent_id: str) -> str | None:
    """The stage key `agent_id` resolves under absent a workflow stage.

    None for agents with no AI configuration surface — the deterministic
    git_ops agents make no LLM call at all, so there is nothing to resolve.
    """
    return _AGENT_DEFAULT_STAGE.get(agent_id)


def stage_for(extras: dict[str, Any] | None, default: str) -> str:
    """The stage key this run should resolve AI configuration under.

    Prefers the real `Run.workflow_stage` the RunCoordinator injects into
    `AgentContext.extras` (see `run_coordinator.execute_run`), falling back
    to the agent's own default when a run has no workflow — a standalone
    run of the Planning Agent is still, for configuration purposes, the
    "planning" stage.

    The injected value is what makes the review agent resolve correctly:
    the same agent runs as stage `review` in a legacy_sdlc workflow and as
    `ai_pr_review` in an auto-execution workflow, and those are two
    separately configurable rows in the AI Workspace.
    """
    if extras:
        stage = extras.get("stage")
        if stage:
            return str(stage)
    return default


class StageAwareLLMProvider(ILLMProvider):
    """An `ILLMProvider` that resolves its concrete backend per call through
    the AI configuration layer, scoped to one workflow stage.

    Constructed per agent run (cheap — resolution is pure and synchronous;
    no client or connection is created until a request is actually sent).

    `last_resolved` records which provider/model actually served the most
    recent call, including after a fallback hop, so callers that report
    provenance (the Planning Agent's `LLMTrace`, `Run.provider`) can show
    what really answered rather than what was configured as the default.
    """

    def __init__(self, *, stage: str | None, model: str | None = None) -> None:
        self._stage = stage
        self._model = model
        self.last_resolved: ResolvedProvider | None = None

    # -- introspection -----------------------------------------------------

    def preview(self) -> ResolvedProvider:
        """Resolve without sending anything — the same decision `complete()`
        would make. Validates exactly as `create_llm_provider()` does, so an
        unimplemented provider / unknown model / missing API key still fails
        with the same error type and message it did before this indirection
        existed.
        """
        resolved = resolve(model=self._model, stage=self._stage)
        validate_resolution(resolved, requested_model=self._model)
        return resolved

    # -- ILLMProvider ------------------------------------------------------

    async def complete(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        options: LLMRequestOptions | None = None,
    ) -> LLMResponse:
        """Single-shot completion, fully routed through the shared engine.

        Validation runs first so that a bad explicit model or an
        unconfigured provider raises before any network call and before the
        fallback engine would otherwise mask it by hopping to a different
        vendor — a misconfiguration must stay visible, not be silently
        routed around.
        """
        self.preview()  # raises on unimplemented / unknown model / no key

        response, served_by = await complete_with_fallback(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            options=options,
            model=self._model,
            stage=self._stage,
        )
        self.last_resolved = served_by
        logger.info(
            "llm_completed stage=%s provider=%s model=%s source=%s profile=%s",
            self._stage or "-",
            served_by.key,
            served_by.model,
            served_by.source,
            served_by.profile_slug or "-",
        )
        return response

    async def complete_with_tools(
        self,
        *,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[ToolSpec],
    ) -> ToolTurnResult:
        """Native tool-calling turn against the stage-resolved provider.

        No fallback — see the module docstring. Propagates
        NotImplementedError unchanged so existing callers that degrade
        gracefully on providers without tool-calling keep doing so.
        """
        resolved = self.preview()
        self.last_resolved = resolved
        provider = resolved.spec.build(resolved.config)
        return await provider.complete_with_tools(
            system_prompt=system_prompt,
            messages=messages,
            tools=tools,
        )

    async def analyze(self, context: AIContext) -> AIAnalysisResult:
        """Transitional domain-coupled path used by `InvestigationAgent` and
        `AIAnalysisService`, which receive a provider by injection.

        Delegates to the stage-resolved concrete provider. No fallback — the
        prompt is built inside the provider, so there is nothing for the
        shared engine to re-send. Unchanged from previous behaviour except
        that the provider/model are now stage-resolved.
        """
        resolved = self.preview()
        self.last_resolved = resolved
        provider = resolved.spec.build(resolved.config)
        logger.info(
            "llm_analyze stage=%s provider=%s model=%s source=%s",
            self._stage or "-",
            resolved.key,
            resolved.model,
            resolved.source,
        )
        return await provider.analyze(context)


# ---------------------------------------------------------------------------
# Shared single-shot JSON invocation — the one body every freeform agent's
# own `_call_llm` used to duplicate
# ---------------------------------------------------------------------------
#
# Before this, `planning`, `development`, `testing`, `documentation_planning`,
# `engineering_review`, and `code_generation` each carried a module-level
# `_call_llm` whose body was byte-for-byte identical (construct a
# StageAwareLLMProvider, call `.complete()` in JSON mode, remap any AppError
# to that agent's own `<Agent>LLMError` type) except for which stage/error
# class it closed over. That is exactly the "LLM invocation logic duplicated
# across multiple agents" gap this module exists to close — the six
# call sites are kept (test seams like
# `patch("app.agents.planning.agent._call_llm", ...)` depend on each agent
# still exposing its own `_call_llm`), but every one of them now delegates
# its body to `invoke_llm_json` below rather than repeating it.


async def invoke_llm_json(
    *,
    system_prompt: str,
    user_prompt: str,
    stage: str,
    model: str | None,
    error_cls: type[AppError],
    metadata_out: dict[str, Any] | None = None,
) -> str:
    """Single JSON-mode completion through the stage-aware provider, with
    the one piece of per-agent variation (which `AppError` subclass a
    provider failure becomes) taken as a parameter instead of duplicated.

    `metadata_out`, when given, is filled in-place with whichever provider
    actually served the request (including after a fallback hop) plus its
    reported token usage — the exact out-param contract the Planning Agent
    already relied on, generalized so any caller can opt in without
    changing its own return type.

    Raises `error_cls` (constructed from the underlying AppError's message,
    with `.provider_error` carried over) for any provider-layer failure —
    the same remapping every agent performed inline before.
    """
    provider: StageAwareLLMProvider | None = None
    try:
        provider = StageAwareLLMProvider(stage=stage, model=model)
        response = await provider.complete(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            options=LLMRequestOptions(response_format=ResponseFormat.JSON),
        )
    except AppError as exc:
        error = error_cls(exc.message)
        error.provider_error = getattr(exc, "provider_error", None)  # type: ignore[attr-defined]
        raise error from exc

    if metadata_out is not None:
        served = provider.last_resolved if provider is not None else None
        metadata_out["provider"] = served.key if served else ""
        metadata_out["model"] = (served.model if served else None) or response.model_name or ""
        metadata_out["prompt_tokens"] = response.prompt_tokens
        metadata_out["completion_tokens"] = response.completion_tokens
        metadata_out["total_tokens"] = response.total_tokens
    return response.text
