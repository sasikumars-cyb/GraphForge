"""Development Agent — Change Planning capability.

Implements the IAgent protocol for goal=develop_change_plan. Every run:
1. Calls RepositoryDiscoveryTool to discover indexed repos (tool_call evidence).
2. Calls ComponentDiscoveryTool to find all components and topics (graph_traversal evidence).
3. Calls DependencyTraversalTool to map edges and cross-repo coupling (graph_traversal evidence).
4. Synthesizes a structured implementation blueprint using the LLM,
   grounded in the real graph context gathered in steps 1-3 (llm_reasoning evidence).

The agent thinks like a Senior Engineer: Which repos change? Which services?
Which files? Can something be reused? What could break? What order?

Inside a workflow, the immediately-prior Planning stage's *full* structured
result is read directly via get_stage_result() and folded into the prompt
(see app.agents.stage_context.format_planning_block) — not via
workflow_service.build_stage_context()/resolve_freetext(), whose 256-char
truncation (app/context/resolvers/freetext.py) meant Planning's summary
almost never survived intact. context.subject.display_name is still what
gets logged/stored as this run's own goal text, and is the only input for a
standalone run (context.extras has no "workflow" outside one) — this is
additive grounding, not a replacement.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import verification
from app.agents._contract import (
    AgentContext,
    AgentOutput,
    Confidence,
    Evidence,
)
from app.agents.blueprint.factory import BlueprintFactory
from app.agents.development.schemas import (
    AffectedComponent,
    AffectedRepository,
    Dependency,
    DevelopmentPlan,
    ImplementationPhase,
    ReusableImplementation,
    Risk,
)
from app.agents.development.tools import (
    ComponentDiscoveryTool,
    DependencyTraversalTool,
    RepositoryDiscoveryTool,
    format_graph_context,
    to_evidence,
)
from app.agents.git_ops._artifact_reader import get_stage_result
from app.agents.prompt_utils import render_prompt_template
from app.agents.stage_context import format_planning_block
from app.ai.providers.base import LLMRequestOptions, ResponseFormat
from app.ai.providers.factory import create_llm_provider
from app.core.exceptions import AppError
from app.graph.neo4j_repository import Neo4jGraphRepository
from app.graph.session import get_driver

logger = logging.getLogger(__name__)

_PROMPT_VERSION = "1.0"
_PROMPT_DIR = Path(__file__).parent / "prompts"
_MAX_GRAPH_CONTEXT_CHARS = 8_000  # larger budget for detailed blueprint


# ---------------------------------------------------------------------------
# LLM call — development-specific, not shared with other agents
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = (
    "You are a Principal Software Engineer. "
    "Respond ONLY with valid JSON matching the requested schema. "
    "Do not include markdown fences or commentary outside the JSON object."
)


class DevelopmentLLMError(AppError):
    status_code = 502
    error_code = "development_llm_error"


async def _call_llm(user_prompt: str, model: str | None = None) -> str:
    """Send a single JSON-mode completion through the configured AI
    provider and return the raw content string. See planning/agent.py's
    `_call_llm` for the full rationale — identical shape."""
    try:
        provider = create_llm_provider(model=model)
        response = await provider.complete(
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            options=LLMRequestOptions(response_format=ResponseFormat.JSON),
        )
    except AppError as exc:
        error = DevelopmentLLMError(exc.message)
        error.provider_error = getattr(exc, "provider_error", None)  # type: ignore[attr-defined]
        raise error from exc
    return response.text


# ---------------------------------------------------------------------------
# Prompt rendering
# ---------------------------------------------------------------------------


def _render_prompt(task_description: str, graph_context: str) -> str:
    """Render the development.md template with the given variables."""
    return render_prompt_template(
        _PROMPT_DIR / "development.md", task_description, graph_context, _MAX_GRAPH_CONTEXT_CHARS
    )


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


def _parse_llm_response(raw: str, goal: str) -> DevelopmentPlan:
    """Parse the LLM's JSON response into a DevelopmentPlan."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DevelopmentLLMError(f"LLM response is not valid JSON: {exc}") from exc

    repositories = [
        AffectedRepository(
            name=r.get("name", ""),
            owner=r.get("owner", ""),
            reason=r.get("reason", ""),
        )
        for r in data.get("repositories", [])
    ]

    components = [
        AffectedComponent(
            name=c.get("name", ""),
            component_type=c.get("component_type", ""),
            repository=c.get("repository", ""),
            file_path=c.get("file_path", ""),
            change_description=c.get("change_description", ""),
        )
        for c in data.get("components", [])
    ]

    dependencies = [
        Dependency(
            source=d.get("source", ""),
            target=d.get("target", ""),
            relationship=d.get("relationship", ""),
            risk_note=d.get("risk_note", ""),
        )
        for d in data.get("dependencies", [])
    ]

    reusable = [
        ReusableImplementation(
            name=r.get("name", ""),
            repository=r.get("repository", ""),
            reason=r.get("reason", ""),
        )
        for r in data.get("reusable_implementations", [])
    ]

    phases = [
        ImplementationPhase(
            order=p.get("order", i + 1),
            title=p.get("title", ""),
            description=p.get("description", ""),
            affected_components=p.get("affected_components", []),
            estimated_complexity=p.get("estimated_complexity", ""),
            depends_on_phases=p.get("depends_on_phases", []),
        )
        for i, p in enumerate(data.get("implementation_phases", []))
    ]

    risks = [
        Risk(
            description=r.get("description", ""),
            severity=r.get("severity", ""),
            affected_component=r.get("affected_component", ""),
            mitigation=r.get("mitigation", ""),
        )
        for r in data.get("risks", [])
    ]

    return DevelopmentPlan(
        goal=goal,
        executive_summary=data.get("executive_summary", ""),
        repositories=repositories,
        components=components,
        dependencies=dependencies,
        reusable_implementations=reusable,
        implementation_phases=phases,
        risks=risks,
        recommendations=data.get("recommendations", []),
        graph_context_used=bool(data.get("graph_context_used", False)),
        repositories_consulted=[],  # filled by the agent
        prompt_version=_PROMPT_VERSION,
    )


# ---------------------------------------------------------------------------
# Development Agent
# ---------------------------------------------------------------------------


class DevelopmentAgent:
    """Implements IAgent for goal=develop_change_plan.

    Stateless singleton — db session and Neo4j driver are resolved per-run
    from context.extras["db"] and get_driver().
    """

    async def run(self, context: AgentContext) -> AgentOutput:
        task_description: str = context.subject.display_name
        subject_id: str = context.subject.subject_id

        logger.info(
            "development_agent_started subject_id=%s task=%.80s model=%s",
            subject_id, task_description, context.model,
        )

        db: AsyncSession = context.extras["db"]
        driver = get_driver()
        graph_repo = Neo4jGraphRepository(driver)

        evidence: list[Evidence] = []

        # ------------------------------------------------------------------
        # Step 0 — Read the Planning stage's full result, when this run is
        # part of a workflow (context.extras["workflow"] is only set there;
        # see workflows.py's schedule_run_execution calls). Untruncated —
        # see module docstring for why that matters.
        # ------------------------------------------------------------------
        workflow = context.extras.get("workflow")
        planning_result = get_stage_result(workflow, "planning") if workflow else None
        prior_stage_context = format_planning_block(planning_result) if planning_result else ""
        if workflow is not None:
            evidence.append(
                Evidence(
                    kind="tool_call",
                    reference="read_prior_stage_context",
                    summary=(
                        "Read the full Planning stage result via get_stage_result()."
                        if planning_result
                        else "No completed Planning stage result was available to read."
                    ),
                )
            )

        # ------------------------------------------------------------------
        # Step 1 — Discover indexed repositories (tool_call evidence)
        # ------------------------------------------------------------------
        repos_tool = RepositoryDiscoveryTool(db=db, graph_repository=graph_repo)
        repos_obs = await repos_tool.execute()
        evidence.append(to_evidence(repos_obs, "tool_call"))

        indexed_repos: list[dict[str, str]] = repos_obs.data.get("indexed_repositories", [])
        logger.info("development_agent_step1 indexed_repo_count=%d", len(indexed_repos))

        # ------------------------------------------------------------------
        # Step 2 — Discover components (graph_traversal evidence)
        # ------------------------------------------------------------------
        components_tool = ComponentDiscoveryTool(graph_repository=graph_repo)
        components_obs = await components_tool.execute(indexed_repos)
        evidence.append(to_evidence(components_obs, "graph_traversal"))

        component_count = len(components_obs.data.get("components", []))
        topic_count = len(components_obs.data.get("kafka_topics", []))
        logger.info(
            "development_agent_step2 component_count=%d topic_count=%d",
            component_count, topic_count,
        )

        # ------------------------------------------------------------------
        # Step 3 — Traverse dependencies (graph_traversal evidence)
        # ------------------------------------------------------------------
        deps_tool = DependencyTraversalTool(graph_repository=graph_repo)
        deps_obs = await deps_tool.execute(indexed_repos)
        evidence.append(to_evidence(deps_obs, "graph_traversal"))

        edge_count = deps_obs.data.get("total_edges", 0)
        cross_repo_count = len(deps_obs.data.get("cross_repo_edges", []))
        logger.info(
            "development_agent_step3 edge_count=%d cross_repo_couplings=%d",
            edge_count, cross_repo_count,
        )

        # ------------------------------------------------------------------
        # Observe: determine confidence
        # ------------------------------------------------------------------
        graph_unavailable = not repos_obs.succeeded or (
            bool(indexed_repos) and not components_obs.succeeded and not deps_obs.succeeded
        )
        has_graph_data = (
            not graph_unavailable
            and bool(indexed_repos)
            and (component_count > 0 or topic_count > 0 or edge_count > 0)
        )

        if graph_unavailable:
            base_confidence = 0.25
        elif has_graph_data:
            base_confidence = 0.85
            # Boost for rich graph data
            if cross_repo_count > 0:
                base_confidence = 0.90
        else:
            base_confidence = 0.40

        # ------------------------------------------------------------------
        # Synthesize: LLM call with full graph context
        # ------------------------------------------------------------------
        graph_context_text = format_graph_context(repos_obs, components_obs, deps_obs)
        prompt_task_description = (
            f"{task_description}\n\n{prior_stage_context}" if prior_stage_context else task_description
        )
        prompt = _render_prompt(prompt_task_description, graph_context_text)

        logger.info(
            "development_agent_synthesizing has_graph_data=%s graph_context_chars=%d",
            has_graph_data, len(graph_context_text),
        )

        try:
            raw_response = await _call_llm(user_prompt=prompt, model=context.model)
            plan = _parse_llm_response(raw_response, task_description)
        except DevelopmentLLMError as exc:
            logger.error("development_agent_llm_failed error=%s", str(exc))
            raise

        # Back-fill repositories_consulted
        plan.repositories_consulted = [r["name"] for r in indexed_repos]

        # Never trust the LLM's self-reported graph_context_used — derive it
        # from what the tools actually returned.
        plan.graph_context_used = has_graph_data

        # ------------------------------------------------------------------
        # Verify the LLM's specific claims against this run's own tool
        # evidence (see app.agents.verification) — generic string
        # membership, no source-code parsing, applies to any repo language.
        # ------------------------------------------------------------------
        evidence_pool = verification.build_evidence_pool(
            [r["name"] for r in indexed_repos],
            [c.get("name", "") for c in components_obs.data.get("components", [])],
            [c.get("file_path", "") for c in components_obs.data.get("components", [])],
            [t.get("name", "") for t in components_obs.data.get("kafka_topics", [])],
        )
        verification_warnings: list[str] = []
        repo_check = verification.verify_claims([r.name for r in plan.repositories], evidence_pool)
        for name in repo_check.unverified:
            verification_warnings.append(
                f"Repository '{name}' cited in this plan was not found among the "
                "repositories this run's graph traversal actually returned — unverified."
            )
        for comp in plan.components:
            comp_check = verification.verify_claims([comp.name, comp.file_path], evidence_pool)
            for claim in comp_check.unverified:
                verification_warnings.append(
                    f"Component claim '{claim}' (for '{comp.name}') does not appear in "
                    "this run's indexed graph data — unverified."
                )
        plan.verification_warnings = verification_warnings
        if verification_warnings:
            evidence.append(
                Evidence(
                    kind="tool_call",
                    reference="claim_verification",
                    summary=(
                        f"{len(verification_warnings)} claim(s) in this implementation "
                        "blueprint could not be verified against this run's own tool "
                        "evidence — see verification_warnings."
                    ),
                )
            )

        # Generate visual blueprint from structured plan fields
        try:
            blueprint = BlueprintFactory.from_development_result(plan)
            plan.blueprint = blueprint.model_dump()
        except Exception:
            logger.warning("development_agent_blueprint_generation_failed", exc_info=True)
            plan.blueprint = None

        # LLM synthesis evidence
        evidence.append(
            Evidence(
                kind="llm_reasoning",
                reference="llm_synthesis",
                summary=(
                    f"LLM produced implementation blueprint with "
                    f"{len(plan.implementation_phases)} phase(s), "
                    f"{len(plan.components)} affected component(s), "
                    f"and {len(plan.risks)} risk(s) for: {task_description[:60]}"
                ),
            )
        )

        # ------------------------------------------------------------------
        # Confidence scoring
        # ------------------------------------------------------------------
        confidence_score = base_confidence
        if plan.implementation_phases:
            confidence_score = min(confidence_score + 0.05, 1.0)

        if graph_unavailable:
            confidence_reasoning = (
                "Knowledge Graph was unavailable (infrastructure error); "
                "blueprint is based on general engineering practices only. "
                "Retry when the graph service is restored."
            )
        elif has_graph_data:
            kafka_clause = f", {topic_count} Kafka topic(s)," if topic_count else ""
            confidence_reasoning = (
                f"Graph traversal found {component_count} component(s){kafka_clause} "
                f"and {edge_count} edge(s) "
                f"across {len(indexed_repos)} indexed "
                f"repositor{'y' if len(indexed_repos) == 1 else 'ies'}. "
            )
            if cross_repo_count > 0:
                confidence_reasoning += (
                    f"Identified {cross_repo_count} cross-repository coupling(s). "
                )
            confidence_reasoning += "Blueprint is grounded in real architecture data."
        else:
            confidence_reasoning = (
                f"Graph is healthy but contains no architecture data "
                f"({len(indexed_repos)} indexed "
                f"repositor{'y' if len(indexed_repos) == 1 else 'ies'}, "
                f"0 components, 0 edges). "
                "Blueprint uses general engineering practices."
            )

        logger.info(
            "development_agent_completed subject_id=%s confidence=%.2f "
            "evidence_count=%d phases=%d components=%d risks=%d",
            subject_id, confidence_score, len(evidence),
            len(plan.implementation_phases), len(plan.components), len(plan.risks),
        )

        return AgentOutput(
            agent_id="development",
            subject_id=subject_id,
            confidence=Confidence(
                score=confidence_score,
                reasoning=confidence_reasoning,
            ),
            evidence=evidence,
            result=plan.model_dump(),
            prompt_version=_PROMPT_VERSION,
        )
