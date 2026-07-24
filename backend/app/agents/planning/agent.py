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

from app.agents.planning.schemas import ImplementationStep, PlanningResult
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
_MAX_GRAPH_CONTEXT_CHARS = 6_000  # keep prompt budget under control

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


def _render_prompt(task_description: str, graph_context: str) -> str:
    """Render the planning.md template with the given variables."""
    return render_prompt_template(
        _PROMPT_DIR / "planning.md", task_description, graph_context, _MAX_GRAPH_CONTEXT_CHARS
    )


# ---------------------------------------------------------------------------
# PlanningResult parsing
# ---------------------------------------------------------------------------


def _parse_llm_response(raw: str, task_description: str) -> PlanningResult:
    """Parse the LLM's JSON response into a PlanningResult.

    Never silently ignores a malformed response — raises PlanningLLMError
    if the JSON can't be parsed. Fills in defaults for missing optional fields.
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PlanningLLMError(f"LLM response is not valid JSON: {exc}") from exc

    steps = [
        ImplementationStep(
            order=s.get("order", i + 1),
            description=s.get("description", ""),
            affected_component=s.get("affected_component", ""),
            risk_note=s.get("risk_note", ""),
        )
        for i, s in enumerate(data.get("implementation_steps", []))
    ]

    return PlanningResult(
        task_description=task_description,
        executive_summary=data.get("executive_summary", ""),
        implementation_steps=steps,
        affected_components=data.get("affected_components", []),
        kafka_topics_involved=data.get("kafka_topics_involved", []),
        risk_considerations=data.get("risk_considerations", []),
        graph_context_used=bool(data.get("graph_context_used", False)),
        repositories_consulted=[],  # filled in below by the agent
        prompt_version=_PROMPT_VERSION,
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
        graph_context_text = format_graph_context(repos_obs, traverse_obs)
        prompt = _render_prompt(task_description, graph_context_text)

        logger.info(
            "planning_agent_synthesizing has_graph_data=%s graph_context_chars=%d",
            has_graph_data, len(graph_context_text),
        )

        try:
            raw_response = await _call_llm(user_prompt=prompt, model=context.model)
            planning_result = _parse_llm_response(raw_response, task_description)
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
