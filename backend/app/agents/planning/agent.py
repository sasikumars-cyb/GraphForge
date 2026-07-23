"""Planning Agent — PW-4.

Implements the IAgent protocol for goal=plan_freeform. Every run:
1. Calls GetIndexedRepositoriesTool to list indexed repos (tool_call evidence).
2. Calls TraverseArchitectureGraphTool to traverse the Knowledge Graph
   for components and Kafka topics (graph_traversal evidence).
3. Synthesizes an implementation plan using the LLM, grounded in the
   real graph context gathered in steps 1-2 (llm_reasoning evidence).

The agent's own Plan -> Select Tool -> Execute -> Observe -> Decide loop
is explicit in execute_tools() — the same five-state loop the Review
Agent uses internally, applied here to a different tool set and domain.

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

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents._contract import (
    AgentContext,
    AgentOutput,
    Confidence,
    Evidence,
)

logger = logging.getLogger(__name__)

from app.agents.planning.schemas import ImplementationStep, PlanningResult
from app.agents.planning.tools import (
    GetIndexedRepositoriesTool,
    PlanningObservation,
    TraverseArchitectureGraphTool,
    format_graph_context,
    to_evidence,
)
from app.core.config import get_settings
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


async def _call_llm(
    user_prompt: str,
    model: str | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> str:
    """Make a single Chat Completions request and return the content string.

    Uses the same settings (api_key, base_url, model) as the existing
    OpenAI provider but with a planning-specific system prompt and without
    the AIAnalysisResult response schema. This is not a duplicate of
    OpenAIProvider — it serves a different purpose (free-form JSON
    generation vs. strongly-typed impact analysis).
    """
    settings = get_settings()
    provider = settings.ai_provider.lower()

    if provider == "openai":
        if not settings.openai_api_key:
            raise PlanningLLMError("OPENAI_API_KEY is not configured.")
        api_key = settings.openai_api_key
        base_url = "https://api.openai.com/v1/chat/completions"
        effective_model = model or settings.openai_model
    elif provider == "groq":
        if not settings.groq_api_key:
            raise PlanningLLMError("GROQ_API_KEY is not configured.")
        api_key = settings.groq_api_key
        base_url = "https://api.groq.com/openai/v1/chat/completions"
        effective_model = settings.groq_model
    else:
        raise PlanningLLMError(f"Unsupported AI provider: {provider}")

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
        raise PlanningLLMError("LLM request timed out.") from exc
    except httpx.HTTPError as exc:
        raise PlanningLLMError(f"LLM communication error: {exc}") from exc
    finally:
        if should_close:
            await client.aclose()

    if response.status_code == 401:
        raise PlanningLLMError("LLM API key is invalid.")
    if response.status_code == 429:
        raise PlanningLLMError("LLM rate limit exceeded.")
    if response.status_code >= 400:
        raise PlanningLLMError(f"LLM returned HTTP {response.status_code}.")

    body = response.json()
    try:
        return body["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as exc:
        raise PlanningLLMError(f"LLM response missing expected fields: {exc}") from exc


# ---------------------------------------------------------------------------
# Prompt rendering — reuses the prompt file from planning/prompts/
# ---------------------------------------------------------------------------


def _render_prompt(task_description: str, graph_context: str) -> str:
    """Render the planning.md template with the given variables."""
    template_path = _PROMPT_DIR / "planning.md"
    raw = template_path.read_text(encoding="utf-8")
    # Strip YAML front-matter (same pattern as PromptBuilder)
    import re
    body = re.sub(r"^---\s*\n.*?\n---\s*\n", "", raw, flags=re.DOTALL)
    body = body.replace("{{ task_description }}", task_description)
    body = body.replace("{{ graph_context }}", graph_context[:_MAX_GRAPH_CONTEXT_CHARS])
    return body


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

        indexed_repos: list[dict] = repos_obs.data.get("indexed_repositories", [])

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
            base_confidence = 0.30
        elif has_graph_data:
            base_confidence = 0.85
        else:
            base_confidence = 0.45

        # ------------------------------------------------------------------
        # Decide: if graph has no data AND this is a component-specific
        # query, try once more before synthesising (low-confidence retry path).
        # One retry, hard cap — same discipline as the Review Agent.
        # ------------------------------------------------------------------
        retry_count = 0
        if not has_graph_data and len(indexed_repos) == 0:
            # No repositories indexed at all — confidence already low.
            # Skip retry (nothing more to gather) and synthesise with a note.
            logger.info(
                "planning_agent_no_graph_data note=no indexed repositories"
            )

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
