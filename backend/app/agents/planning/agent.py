"""Planning Agent — PW-4.

Implements the IAgent protocol for goal=plan_freeform. Every run:
1. Calls GetIndexedRepositoriesTool to list indexed repos (tool_call evidence).
2. Calls TraverseArchitectureGraphTool to traverse the Knowledge Graph
   for components and Kafka topics (graph_traversal evidence).
3. Synthesizes an implementation plan using the LLM, grounded in the
   real graph context gathered in steps 1-2 (llm_reasoning evidence).

The agent's own Plan -> Select Tool -> Execute -> Observe -> Decide loop
is explicit inline in run() below — the same five-state shape the Review
Agent uses internally, applied here to a different tool set and domain.
No retry is implemented in this phase (unlike the Review Agent's
confidence-triggered retry) — a single pass always runs to completion.

The key demonstration requirement (RAJAN_PACKAGE.md): at least one
Evidence entry must be kind="graph_traversal" or kind="tool_call". This
is guaranteed: step 1 always produces a tool_call entry and step 2
always produces a graph_traversal entry, even if those queries return
empty results (empty = real graph query, just no data yet).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents._contract import (
    AgentContext,
    AgentOutput,
    Confidence,
    Evidence,
)
from app.agents.prompt_utils import render_prompt_template
from app.ai.providers.base import LLMRequestOptions, ResponseFormat
from app.ai.providers.factory import create_llm_provider

logger = logging.getLogger(__name__)

from app.agents.blueprint.factory import BlueprintFactory
from app.agents.planning.classifier import PlanningProfile, analyse, pattern_for_key
from app.agents.planning.schemas import (
    ArchitectureLayer,
    DataEntity,
    DataFlowStep,
    ImplementationPhase,
    ImplementationStep,
    PlanningResult,
    RepositoryUsage,
    RiskItem,
)
from app.agents.planning.tools import (
    GetIndexedRepositoriesTool,
    PlanningObservation,
    TraverseArchitectureGraphTool,
    format_graph_context,
    to_evidence,
)
from app.core.exceptions import AppError
from app.graph.neo4j_repository import Neo4jGraphRepository
from app.graph.session import get_driver

_PROMPT_VERSION = "1.0"
_PROMPT_DIR = Path(__file__).parent / "prompts"
# Deliberately tight. A large repository dump ahead of the design
# instruction is what caused the LLM to anchor on existing services and
# produce repository-first plans; capping it both saves tokens and keeps
# the architecture driven by the business brief.
_MAX_GRAPH_CONTEXT_CHARS = 3_500

# ---------------------------------------------------------------------------
# LLM call — minimal, planning-specific, not shared with the Review Agent
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = (
    "You are a senior software architect. "
    "Respond ONLY with valid JSON matching the requested schema. "
    "Do not include markdown fences or commentary outside the JSON object."
)


class PlanningLLMError(AppError):
    status_code = 502
    error_code = "planning_llm_error"


async def _call_llm(user_prompt: str, model: str | None = None) -> str:
    """Send a single JSON-mode completion through the configured AI
    provider and return the raw content string.

    Transport is entirely delegated to create_llm_provider()/
    Provider.complete() — the one LLM transport implementation in this
    codebase. Any provider-level failure (auth, rate limit, timeout,
    malformed response, unconfigured provider) is remapped to
    `PlanningLLMError` so existing callers/tests keep seeing this agent's
    own error type; kept as a module-level function so existing test
    seams (`patch("...agent._call_llm", ...)`) stay stable.
    """
    try:
        provider = create_llm_provider(model=model)
        response = await provider.complete(
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            options=LLMRequestOptions(response_format=ResponseFormat.JSON),
        )
    except AppError as exc:
        error = PlanningLLMError(exc.message)
        error.provider_error = getattr(exc, "provider_error", None)  # type: ignore[attr-defined]
        raise error from exc
    return response.text


# ---------------------------------------------------------------------------
# Prompt rendering — reuses the prompt file from planning/prompts/
# ---------------------------------------------------------------------------


def _render_prompt(
    task_description: str, graph_context: str, profile: PlanningProfile
) -> str:
    """Render the planning.md template with the given variables.

    The capability placeholders are substituted here rather than in
    `render_prompt_template` because that helper is shared by five agents and
    only Planning does capability analysis. The playbook is composed from the
    detected capabilities alone, so its size scales with the brief instead of
    being a fixed block per project type.
    """
    body = render_prompt_template(
        _PROMPT_DIR / "planning.md", task_description, graph_context, _MAX_GRAPH_CONTEXT_CHARS
    )
    capabilities = ", ".join(profile.capability_labels) or "none detected — derive from the brief"
    body = body.replace("{{ architecture_pattern }}", profile.label)
    body = body.replace("{{ detected_capabilities }}", capabilities)
    body = body.replace("{{ architecture_playbook }}", profile.playbook())
    return body


# ---------------------------------------------------------------------------
# PlanningResult parsing
# ---------------------------------------------------------------------------


def _safe_list(raw_data: object, model_cls: type) -> list:
    """Parse a list of dicts from LLM output into model instances.

    Silently skips items that are not dicts or fail model validation —
    LLM output may contain extra keys or null values; extra="ignore" on the
    models handles schema mismatches; this handles structural mismatches.
    """
    if not isinstance(raw_data, list):
        return []
    result = []
    for item in raw_data:
        if not isinstance(item, dict):
            continue
        try:
            result.append(model_cls(**item))
        except Exception:
            pass
    return result


def _parse_llm_response(
    raw: str, task_description: str, profile: PlanningProfile | None = None
) -> PlanningResult:
    """Parse the LLM's JSON response into a PlanningResult.

    Never silently ignores a malformed response — raises PlanningLLMError
    if the JSON can't be parsed. Fills in defaults for missing optional fields.

    `profile` is the capability analysis that shaped the prompt. The LLM
    echoes an `architecture_pattern` back; when it names a pattern we
    recognise, its answer wins (it read the whole brief, the analyser only
    matched keywords). Anything unrecognised keeps the derived pattern.
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PlanningLLMError(f"LLM response is not valid JSON: {exc}") from exc

    analysis = profile or analyse("")
    pattern = analysis.pattern
    llm_pattern = data.get("architecture_pattern")
    if isinstance(llm_pattern, str) and llm_pattern.strip():
        key = llm_pattern.strip().lower()
        resolved = pattern_for_key(key)
        # pattern_for_key returns generic for unknown keys — only trust the
        # LLM when it actually named a pattern we know about.
        if resolved.key != "generic" or key == "generic":
            pattern = resolved

    # Capabilities stay deterministic: they come from the brief, not the
    # model, so they cannot be talked out of a requirement the brief states.
    capability_labels = analysis.capability_labels

    steps = [
        ImplementationStep(
            order=s.get("order", i + 1),
            description=s.get("description", ""),
            affected_component=s.get("affected_component", ""),
            risk_note=s.get("risk_note", ""),
        )
        for i, s in enumerate(data.get("implementation_steps", []))
        if isinstance(s, dict)
    ]

    return PlanningResult(
        task_description=task_description,
        executive_summary=data.get("executive_summary", ""),
        implementation_steps=steps,
        affected_components=data.get("affected_components") or [],
        kafka_topics_involved=data.get("kafka_topics_involved") or [],
        risk_considerations=data.get("risk_considerations") or [],
        graph_context_used=bool(data.get("graph_context_used", False)),
        repositories_consulted=[],  # filled in below by the agent
        prompt_version=_PROMPT_VERSION,
        capabilities=capability_labels,
        project_type=pattern.key,
        project_type_label=pattern.label,
        # Architect-level blueprint fields — empty when LLM doesn't produce them
        architecture_layers=_safe_list(data.get("architecture_layers"), ArchitectureLayer),
        data_flow=_safe_list(data.get("data_flow"), DataFlowStep),
        repository_usage=_safe_list(data.get("repository_usage"), RepositoryUsage),
        data_entities=_safe_list(data.get("data_entities"), DataEntity),
        implementation_phases=_safe_list(data.get("implementation_phases"), ImplementationPhase),
        risks=_safe_list(data.get("risks"), RiskItem),
    )


# ---------------------------------------------------------------------------
# Planning Agent
# ---------------------------------------------------------------------------


class PlanningAgent:
    """Implements IAgent for goal=plan_freeform.

    Stateless singleton — db session and Neo4j driver are resolved per-run
    from context.extras["db"] and get_driver(). Registered with the global
    registry at startup via register_agents().
    """

    async def run(self, context: AgentContext) -> AgentOutput:
        task_description: str = context.subject.display_name
        subject_id: str = context.subject.subject_id

        logger.info(
            "planning_agent_started subject_id=%s task=%.80s model=%s",
            subject_id, task_description, context.model,
        )

        db: AsyncSession = context.extras["db"]
        driver = get_driver()
        graph_repo = Neo4jGraphRepository(driver)

        evidence: list[Evidence] = []

        # ------------------------------------------------------------------
        # Analyse the business problem FIRST — before any repository data is
        # fetched or formatted. This ordering is the fix for repository-first
        # planning: capabilities and the architecture pattern are derived from
        # the brief, so an ETL request gets ETL zones even when every indexed
        # repository is a microservice. The resulting search terms then drive
        # which repositories are worth showing at all.
        # Pure keyword matching — no LLM call, no tokens.
        # ------------------------------------------------------------------
        profile = analyse(task_description)
        logger.info(
            "planning_agent_analysed pattern=%s capabilities=%s",
            profile.key, ",".join(c.key for c in profile.capabilities) or "none",
        )

        # ------------------------------------------------------------------
        # Plan: the agent always runs both tools — the repository list
        # is needed to bound the graph traversal, and the traversal is what
        # produces the graph_traversal evidence required by the Definition of Done.
        # ------------------------------------------------------------------

        # Step 1 — get indexed repositories (tool_call evidence)
        repos_tool = GetIndexedRepositoriesTool(db=db, graph_repository=graph_repo)
        repos_obs = await repos_tool.execute()
        evidence.append(to_evidence(repos_obs, "tool_call"))

        indexed_repos: list[dict[str, str]] = repos_obs.data.get("indexed_repositories", [])

        logger.info(
            "planning_agent_step1 indexed_repo_count=%d",
            len(indexed_repos),
        )

        # Step 2 — traverse the architecture graph (graph_traversal evidence)
        # Runs even if indexed_repos is empty — the resulting observation
        # ("No indexed repositories to traverse") is itself a graph traversal
        # result (a zero-result query is still a graph query).
        traverse_tool = TraverseArchitectureGraphTool(graph_repository=graph_repo)
        traverse_obs = await traverse_tool.execute(indexed_repos)
        evidence.append(to_evidence(traverse_obs, "graph_traversal"))

        component_count: int = len(traverse_obs.data.get("components", []))
        topic_count: int = len(traverse_obs.data.get("kafka_topics", []))

        logger.info(
            "planning_agent_step2 component_count=%d topic_count=%d",
            component_count, topic_count,
        )

        # ------------------------------------------------------------------
        # Observe: determine confidence based on what the graph returned
        # ------------------------------------------------------------------
        # P0-1 / P1-3: distinguish graph unavailable (tool failure) from
        # graph available but empty (healthy zero-result query).
        graph_unavailable = not repos_obs.succeeded or (
            bool(indexed_repos) and not traverse_obs.succeeded
        )
        has_graph_data = (
            not graph_unavailable
            and bool(indexed_repos)
            and (component_count > 0 or topic_count > 0)
        )
        if graph_unavailable:
            base_confidence = 0.25
        elif has_graph_data:
            base_confidence = 0.85
        else:
            base_confidence = 0.40

        # ------------------------------------------------------------------
        # Decide: no retry is implemented in this phase — a single pass
        # always proceeds to synthesis, whatever the graph returned. (An
        # earlier draft of this method claimed a Review-Agent-style
        # confidence-triggered retry here; that was never implemented, so
        # the claim was removed rather than left misleading.)
        # ------------------------------------------------------------------
        if not indexed_repos:
            logger.info("planning_agent_no_graph_data note=no indexed repositories")

        # ------------------------------------------------------------------
        # Synthesize: LLM call with real graph context
        # ------------------------------------------------------------------
        # Repository search is capability-driven: only repositories that match
        # the capabilities the architecture needs reach the prompt. This is the
        # "repositories validate the architecture, never define it" step.
        graph_context_text = format_graph_context(
            repos_obs, traverse_obs, relevance_terms=profile.search_terms
        )
        prompt = _render_prompt(task_description, graph_context_text, profile)

        logger.info(
            "planning_agent_synthesizing has_graph_data=%s graph_context_chars=%d pattern=%s",
            has_graph_data, len(graph_context_text), profile.key,
        )

        try:
            raw_response = await _call_llm(user_prompt=prompt, model=context.model)
            planning_result = _parse_llm_response(raw_response, task_description, profile)
        except PlanningLLMError as exc:
            # LLM failure — fail cleanly, per AGENT_FRAMEWORK.md error policy.
            logger.error("planning_agent_llm_failed error=%s", str(exc))
            raise

        # Back-fill repositories_consulted from the graph traversal
        planning_result.repositories_consulted = [r["name"] for r in indexed_repos]

        # Never trust the LLM's self-reported graph_context_used — derive it
        # from what the tools actually returned. If traversal failed or the
        # graph was empty, this must be False regardless of what the model claims.
        planning_result.graph_context_used = has_graph_data

        # Generate visual blueprint from the structured result. Runs after
        # repositories_consulted and graph_context_used are finalized so
        # factory has the complete picture. Never blocks workflow completion
        # on failure — blueprint is a presentation layer, not core output.
        try:
            blueprint = BlueprintFactory.from_planning_result(planning_result)
            planning_result.blueprint = blueprint.model_dump()
        except Exception:
            logger.warning("planning_agent_blueprint_generation_failed", exc_info=True)
            planning_result.blueprint = None

        # LLM synthesis step is always the last evidence entry
        if graph_unavailable:
            confidence_reasoning = (
                "Knowledge Graph was unavailable (infrastructure error); "
                "plan is based on general engineering practices only. "
                "Retry when the graph service is restored."
            )
        elif has_graph_data:
            confidence_reasoning = (
                f"Graph traversal found {component_count} component(s) and "
                f"{topic_count} Kafka topic(s) across {len(indexed_repos)} "
                f"indexed repositor{'y' if len(indexed_repos) == 1 else 'ies'}. "
                "Plan is grounded in real graph data."
            )
        else:
            confidence_reasoning = (
                f"Graph is healthy but contains no architecture data "
                f"({len(indexed_repos)} indexed repositor{'y' if len(indexed_repos) == 1 else 'ies'}, "
                f"0 components, 0 topics). "
                "Plan uses general engineering practices."
            )
        evidence.append(
            Evidence(
                kind="llm_reasoning",
                reference="llm_synthesis",
                summary=(
                    f"LLM synthesized a {len(planning_result.implementation_steps)}-step "
                    f"plan for: {task_description[:80]}"
                ),
            )
        )

        confidence_score = base_confidence
        if planning_result.implementation_steps:
            # Bump confidence slightly if the LLM produced structured steps
            confidence_score = min(confidence_score + 0.05, 1.0)

        logger.info(
            "planning_agent_completed subject_id=%s confidence=%.2f evidence_count=%d step_count=%d graph_context_used=%s",
            subject_id, confidence_score, len(evidence),
            len(planning_result.implementation_steps), has_graph_data,
        )

        return AgentOutput(
            agent_id="planning",
            subject_id=subject_id,
            confidence=Confidence(
                score=confidence_score,
                reasoning=confidence_reasoning,
            ),
            evidence=evidence,
            result=planning_result.model_dump(),
            prompt_version=_PROMPT_VERSION,
        )
