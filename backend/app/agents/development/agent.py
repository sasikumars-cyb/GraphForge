"""Development Agent — Change Planning capability.

Implements the IAgent protocol for goal=develop_change_plan. Every run:
1. Calls RepositoryDiscoveryTool to discover indexed repos (tool_call evidence).
2. Calls ComponentDiscoveryTool to find all components and topics (graph_traversal evidence).
3. Calls DependencyTraversalTool to map edges and cross-repo coupling (graph_traversal evidence).
4. Synthesizes a structured implementation blueprint using the LLM,
   grounded in the real graph context gathered in steps 1-3 (llm_reasoning evidence).

The agent thinks like a Senior Engineer: Which repos change? Which services?
Which files? Can something be reused? What could break? What order?
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents._contract import (
    AgentContext,
    AgentOutput,
    Confidence,
    Evidence,
)
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
    DevelopmentObservation,
    RepositoryDiscoveryTool,
    format_graph_context,
    to_evidence,
)
from app.core.config import get_settings
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


async def _call_llm(
    user_prompt: str,
    model: str | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> str:
    """Make a single Chat Completions request and return the content string."""
    settings = get_settings()
    provider = settings.ai_provider.lower()

    if provider == "openai":
        if not settings.openai_api_key:
            raise DevelopmentLLMError("OPENAI_API_KEY is not configured.")
        api_key = settings.openai_api_key
        base_url = "https://api.openai.com/v1/chat/completions"
        effective_model = model or settings.openai_model
    elif provider == "groq":
        if not settings.groq_api_key:
            raise DevelopmentLLMError("GROQ_API_KEY is not configured.")
        api_key = settings.groq_api_key
        base_url = "https://api.groq.com/openai/v1/chat/completions"
        effective_model = settings.groq_model
    else:
        raise DevelopmentLLMError(f"Unsupported AI provider: {provider}")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": effective_model,
        "temperature": settings.openai_temperature,
        "max_tokens": settings.openai_max_tokens,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
    }

    client = http_client or httpx.AsyncClient()
    should_close = http_client is None

    try:
        response = await client.post(
            base_url, headers=headers, json=payload, timeout=60.0
        )
    except httpx.TimeoutException as exc:
        raise DevelopmentLLMError("LLM request timed out.") from exc
    except httpx.HTTPError as exc:
        raise DevelopmentLLMError(f"LLM communication error: {exc}") from exc
    finally:
        if should_close:
            await client.aclose()

    if response.status_code == 401:
        raise DevelopmentLLMError("LLM API key is invalid.")
    if response.status_code == 429:
        raise DevelopmentLLMError("LLM rate limit exceeded.")
    if response.status_code >= 400:
        raise DevelopmentLLMError(f"LLM returned HTTP {response.status_code}.")

    body = response.json()
    try:
        return body["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as exc:
        raise DevelopmentLLMError(f"LLM response missing expected fields: {exc}") from exc


# ---------------------------------------------------------------------------
# Prompt rendering
# ---------------------------------------------------------------------------


def _render_prompt(task_description: str, graph_context: str) -> str:
    """Render the development.md template with the given variables."""
    template_path = _PROMPT_DIR / "development.md"
    raw = template_path.read_text(encoding="utf-8")
    body = re.sub(r"^---\s*\n.*?\n---\s*\n", "", raw, flags=re.DOTALL)
    body = body.replace("{{ task_description }}", task_description)
    body = body.replace("{{ graph_context }}", graph_context[:_MAX_GRAPH_CONTEXT_CHARS])
    return body


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
        # Step 1 — Discover indexed repositories (tool_call evidence)
        # ------------------------------------------------------------------
        repos_tool = RepositoryDiscoveryTool(db=db, graph_repository=graph_repo)
        repos_obs = await repos_tool.execute()
        evidence.append(to_evidence(repos_obs, "tool_call"))

        indexed_repos: list[dict] = repos_obs.data.get("indexed_repositories", [])
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
        prompt = _render_prompt(task_description, graph_context_text)

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
            confidence_reasoning = (
                f"Graph traversal found {component_count} component(s), "
                f"{topic_count} Kafka topic(s), and {edge_count} edge(s) "
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
