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
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents._contract import AgentContext
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
from app.ai.providers.pricing import estimate_cost_usd
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

STAGE_CONTEXT_DISCOVERY = "context_discovery"
STAGE_PLANNING = "planning"
STAGE_DEVELOPMENT = "development"
STAGE_TESTING = "testing"
STAGE_REVIEW = "review"
STAGE_DOCUMENTATION_PLANNING = "documentation_planning"
STAGE_ENGINEERING_REVIEW = "engineering_review"
STAGE_GENERATE_CODE = "generate_code"
# Standalone AI Workspace capability (goal=review_documentation) — distinct
# from STAGE_DOCUMENTATION_PLANNING (a Workflow stage). Only an AI-provider-
# config resolution key, unrelated to `app.services.workflow_service.
# STAGE_GOALS` (the actual Workflow pipeline), which this is deliberately
# not added to.
STAGE_DOCUMENTATION_REVIEW = "documentation_review"
# Standalone AI Workspace capability (goal=analyze_documentation_health),
# read-only. Same note as STAGE_DOCUMENTATION_REVIEW above: an AI-provider
# config resolution key only, not a Workflow stage.
STAGE_DOCUMENTATION_HEALTH = "documentation_health"
# Standalone AI Workspace capability (goal=analyze_api_intelligence),
# Markdown-only. Same note as STAGE_DOCUMENTATION_REVIEW above: an
# AI-provider config resolution key only, not a Workflow stage.
STAGE_API_INTELLIGENCE = "api_intelligence"


# agent_id -> the stage key that agent resolves under when a run carries no
# `workflow_stage` (i.e. a standalone run). Each agent already passes its own
# default to `stage_for()`; this map exists so callers *outside* an agent —
# specifically `POST /agent-runs`, which records `Run.provider` before
# dispatch — can predict the same resolution the agent will perform, instead
# of reporting the raw env provider and being wrong whenever a stage override
# or profile applies.
_AGENT_DEFAULT_STAGE: dict[str, str] = {
    "context_discovery": STAGE_CONTEXT_DISCOVERY,
    "planning": STAGE_PLANNING,
    "development": STAGE_DEVELOPMENT,
    "testing": STAGE_TESTING,
    "review": STAGE_REVIEW,
    "documentation_planning": STAGE_DOCUMENTATION_PLANNING,
    "engineering_review": STAGE_ENGINEERING_REVIEW,
    "code_generation": STAGE_GENERATE_CODE,
    "documentation_review": STAGE_DOCUMENTATION_REVIEW,
    "documentation_health": STAGE_DOCUMENTATION_HEALTH,
    "api_intelligence": STAGE_API_INTELLIGENCE,
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
        # Failed provider attempts that preceded the most recent successful
        # `complete()` (0 when the primary answered). Same
        # "record what actually happened" role as `last_resolved`, for the
        # one signal the fallback loop computes but used to discard.
        self.last_retry_count: int = 0

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

        attempts: list[int] = []
        response, served_by = await complete_with_fallback(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            options=options,
            model=self._model,
            stage=self._stage,
            attempts_out=attempts,
        )
        self.last_resolved = served_by
        self.last_retry_count = attempts[0] if attempts else 0
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


# Every key `_fill_invocation_metadata` writes. Documented as a constant so a
# consumer (an agent's own result schema, a UI payload, a future analytics
# table) can be checked against this list rather than against whichever
# subset one agent happened to read.
LLM_INVOCATION_METADATA_KEYS = (
    "provider",
    "model",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "estimated_cost_usd",
    "latency_ms",
    "retry_count",
    "finish_reason",
    "status",
    "started_at",
    "finished_at",
    "error",
)


def _derive_invocation_metadata(
    *,
    provider: StageAwareLLMProvider | None,
    response: LLMResponse | None,
    started: float,
    started_at: datetime,
    error: Exception | None,
) -> dict[str, Any]:
    """Derive one invocation's full observability set — the single place
    these signals are computed, so every agent reports the same fields
    computed the same way. Values are honestly `None` when the serving
    provider didn't report them (token counts, `finish_reason`) or when the
    model isn't in `app.ai.providers.pricing`'s table (`estimated_cost_usd`)
    — never a fabricated stand-in, matching the policy `LLMTrace` already
    documents for the same fields.

    `retry_count` is the number of *failed provider attempts* that preceded
    the successful one (see `app.ai.config.fallback`), not an
    orchestrator-level re-run count.

    Computed unconditionally — independent of whether a caller wants an
    in-memory copy (`metadata_out`) or persistence (ADR 0012) — so neither
    concern can silently disable the other.
    """
    served = provider.last_resolved if provider is not None else None
    finished_at = datetime.now(UTC)

    model = (served.model if served else None) or (response.model_name if response else None) or ""
    prompt_tokens = response.prompt_tokens if response else None
    completion_tokens = response.completion_tokens if response else None
    cost = (
        estimate_cost_usd(model, prompt_tokens, completion_tokens) if response is not None else None
    )

    return {
        "provider": served.key if served else "",
        "model": model,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": response.total_tokens if response else None,
        "finish_reason": response.finish_reason if response else None,
        "estimated_cost_usd": cost.total_usd if cost else None,
        "latency_ms": int((time.monotonic() - started) * 1000),
        "retry_count": provider.last_retry_count if provider is not None else 0,
        "status": "failed" if error is not None else "completed",
        "error": str(getattr(error, "message", error)) if error is not None else None,
        "started_at": started_at,
        "finished_at": finished_at,
    }


async def _fill_invocation_metadata(
    metadata_out: dict[str, Any] | None,
    *,
    provider: StageAwareLLMProvider | None,
    response: LLMResponse | None,
    started: float,
    started_at: datetime,
    error: Exception | None,
    db: AsyncSession | None,
    run_id: uuid.UUID | None,
    agent_step_id: uuid.UUID | None,
    stage: str | None,
    purpose: str,
    sequence: int,
) -> None:
    """Derive one invocation's metadata, optionally copy it into
    `metadata_out` (an agent's own in-memory use, e.g. Planning's
    `LLMTrace`), and — the ADR 0012 persistence pathway — write it to
    `llm_invocations` whenever a db session and the owning run/step are
    available. This is the *only* place in the codebase that writes to
    that table; no agent persists an invocation record itself.
    """
    derived = _derive_invocation_metadata(
        provider=provider, response=response, started=started, started_at=started_at, error=error
    )

    if metadata_out is not None:
        metadata_out.update(derived)
        metadata_out["started_at"] = derived["started_at"].isoformat()
        metadata_out["finished_at"] = derived["finished_at"].isoformat()

    if db is not None and run_id is not None and agent_step_id is not None:
        await persist_llm_invocation(
            db,
            run_id=run_id,
            agent_step_id=agent_step_id,
            stage=stage,
            purpose=purpose,
            sequence=sequence,
            metadata=derived,
            error=error,
        )


async def persist_llm_invocation(
    db: AsyncSession,
    *,
    run_id: uuid.UUID,
    agent_step_id: uuid.UUID,
    stage: str | None,
    purpose: str,
    sequence: int,
    metadata: dict[str, Any],
    error: Exception | None,
) -> None:
    """Write one immutable `LLMInvocation` row (ADR 0012).

    Flushes, does not commit — this reuses whatever transaction the caller
    (always `RunCoordinator`, via `context.extras["db"]`) already owns, so
    the row commits atomically with the rest of that run's own persistence
    (including on the failure path: `RunCoordinator._commit_with_hook`
    always commits, never rolls back, so a failed invocation's row is not
    lost). Never called with a session this function doesn't already share
    with the run/step it's recording — no independent transaction is
    opened here.

    Also updates `AIProviderUsage` (best-effort, swallows its own errors
    per its own docstring) from the same data, at the same call site —
    completing the wiring `app.ai.config.usage.record_outcome` previously
    had zero callers for (ADR 0012, Current Repository State #4).
    """
    from app.ai.config.usage import record_outcome
    from app.models.llm_invocation import LLMInvocation

    invocation = LLMInvocation(
        agent_step_id=agent_step_id,
        run_id=run_id,
        purpose=purpose,
        sequence=sequence,
        provider=metadata["provider"],
        model=metadata["model"],
        stage=stage,
        status=metadata["status"],
        error=metadata["error"],
        prompt_tokens=metadata["prompt_tokens"],
        completion_tokens=metadata["completion_tokens"],
        total_tokens=metadata["total_tokens"],
        estimated_cost_usd=metadata["estimated_cost_usd"],
        finish_reason=metadata["finish_reason"],
        latency_ms=metadata["latency_ms"],
        retry_count=metadata["retry_count"],
        started_at=metadata["started_at"],
        finished_at=metadata["finished_at"],
    )
    db.add(invocation)
    await db.flush()

    if metadata["provider"]:
        # The real exception object, not a reconstruction from its
        # stringified message — record_outcome classifies rate-limit/auth
        # failures by isinstance(), which a synthetic exception could
        # never match.
        await record_outcome(
            db,
            provider_key=metadata["provider"],
            latency_ms=metadata["latency_ms"],
            error=error,
        )


async def invoke_llm_json(
    *,
    system_prompt: str,
    user_prompt: str,
    stage: str,
    model: str | None,
    error_cls: type[AppError],
    metadata_out: dict[str, Any] | None = None,
    context: AgentContext | None = None,
    purpose: str = "initial",
    sequence: int = 0,
) -> str:
    """Single JSON-mode completion through the stage-aware provider, with
    the one piece of per-agent variation (which `AppError` subclass a
    provider failure becomes) taken as a parameter instead of duplicated.

    `metadata_out`, when given, is filled in-place with everything known
    about the invocation — see `LLM_INVOCATION_METADATA_KEYS` for the full
    set and `_fill_invocation_metadata` for how each value is derived. This
    is the single pathway through which *every* agent gets observability;
    no agent should collect these signals itself (that fragmentation is
    exactly what this out-param exists to prevent).

    `context`, when given, is where ADR 0012 persistence reads `db`,
    `run_id`, and `agent_step_id` from — `RunCoordinator.execute_run`/
    `resume_step` inject all three into `AgentContext.extras` (the same
    dict every agent already reads `db`/`user_id`/`stage` from) before
    calling the agent, since only the orchestrator knows the Run/AgentStep
    it just created. Persistence is unconditional whenever all three are
    present — it does not require `metadata_out` to also be given, since
    most agents (everything but Planning) have no in-memory use for the
    metadata but must still be observable. `purpose`/`sequence` distinguish
    more than one invocation per step (see `app.agents.reflection` — a
    reflection call passes `purpose="reflection", sequence=1`).

    Metadata is recorded on the failure path too, not only on success: a
    failed invocation is precisely the one worth observing. On failure the
    caller still receives `error_cls` — the metadata is a side effect, not
    a return value, so no caller's error handling changes.

    Raises `error_cls` (constructed from the underlying AppError's message,
    with `.provider_error` carried over) for any provider-layer failure —
    the same remapping every agent performed inline before.
    """
    db = context.extras.get("db") if context is not None else None
    run_id = context.extras.get("run_id") if context is not None else None
    agent_step_id = context.extras.get("agent_step_id") if context is not None else None

    provider: StageAwareLLMProvider | None = None
    started = time.monotonic()
    started_at = datetime.now(UTC)
    try:
        provider = StageAwareLLMProvider(stage=stage, model=model)
        response = await provider.complete(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            options=LLMRequestOptions(response_format=ResponseFormat.JSON),
        )
    except AppError as exc:
        await _fill_invocation_metadata(
            metadata_out,
            provider=provider,
            response=None,
            started=started,
            started_at=started_at,
            error=exc,
            db=db,
            run_id=run_id,
            agent_step_id=agent_step_id,
            stage=stage,
            purpose=purpose,
            sequence=sequence,
        )
        error = error_cls(exc.message)
        error.provider_error = getattr(exc, "provider_error", None)  # type: ignore[attr-defined]
        raise error from exc

    await _fill_invocation_metadata(
        metadata_out,
        provider=provider,
        response=response,
        started=started,
        started_at=started_at,
        error=None,
        db=db,
        run_id=run_id,
        agent_step_id=agent_step_id,
        stage=stage,
        purpose=purpose,
        sequence=sequence,
    )
    return response.text
