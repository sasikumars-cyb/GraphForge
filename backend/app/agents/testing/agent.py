"""Test Planning Agent — Testing Strategy capability.

Implements the IAgent protocol for goal=plan_tests. Every run:
1. Calls TestRepositoryDiscoveryTool to discover indexed repos (tool_call evidence).
2. Calls TestComponentDiscoveryTool to find components and topics (graph_traversal evidence).
3. Calls TestDependencyTraversalTool to map integration points (graph_traversal evidence).
4. Synthesizes a structured test plan using the LLM, grounded in the
   real graph context gathered in steps 1-3 (llm_reasoning evidence).

The agent thinks like a Senior QA Lead: What changed? Who depends on it?
What could break? Which interfaces require integration tests? What edge
cases are highest risk?

Inside a workflow, the Planning and Development stages' *full* structured
results are read directly via get_stage_result() and folded into the
prompt (see app.agents.stage_context) — not via workflow_service.
build_stage_context()/resolve_freetext(), whose 256-char truncation
(app/context/resolvers/freetext.py) meant almost none of that ever
survived — in practice, only a fragment of Planning's summary, and none of
Development's, which is why test plans previously drifted onto generic
graph-wide component selection instead of what Development actually
changed. context.subject.display_name is still what gets logged/stored as
this run's own goal text, and is the only input for a standalone run
(context.extras has no "workflow" outside one) — this is additive
grounding, not a replacement.
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
from app.agents.git_ops._artifact_reader import get_stage_result
from app.agents.prompt_utils import render_prompt_template
from app.agents.stage_context import format_development_block, format_planning_block
from app.agents.testing.schemas import (
    AutomationCandidate,
    EdgeCase,
    EnvironmentRequirement,
    ExecutionPhase,
    IntegrationTest,
    ManualValidation,
    RegressionTest,
    TestPlan,
    TestRisk,
    TestScope,
)
from app.agents.testing.tools import (
    TestComponentDiscoveryTool,
    TestDependencyTraversalTool,
    TestRepositoryDiscoveryTool,
    format_graph_context,
    to_evidence,
)
from app.ai.providers.base import LLMRequestOptions, ResponseFormat
from app.ai.providers.factory import create_llm_provider
from app.core.exceptions import AppError
from app.graph.neo4j_repository import Neo4jGraphRepository
from app.graph.session import get_driver

logger = logging.getLogger(__name__)

_PROMPT_VERSION = "1.0"
_PROMPT_DIR = Path(__file__).parent / "prompts"
_MAX_GRAPH_CONTEXT_CHARS = 8_000


# ---------------------------------------------------------------------------
# LLM call — testing-specific
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = (
    "You are a Principal QA Engineer. "
    "Respond ONLY with valid JSON matching the requested schema. "
    "Do not include markdown fences or commentary outside the JSON object."
)


class TestingLLMError(AppError):
    status_code = 502
    error_code = "testing_llm_error"


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
        error = TestingLLMError(exc.message)
        error.provider_error = getattr(exc, "provider_error", None)  # type: ignore[attr-defined]
        raise error from exc
    return response.text


# ---------------------------------------------------------------------------
# Prompt rendering
# ---------------------------------------------------------------------------


def _render_prompt(task_description: str, graph_context: str) -> str:
    """Render the testing.md template with the given variables."""
    return render_prompt_template(
        _PROMPT_DIR / "testing.md", task_description, graph_context, _MAX_GRAPH_CONTEXT_CHARS
    )


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


def _parse_llm_response(raw: str, goal: str) -> TestPlan:
    """Parse the LLM's JSON response into a TestPlan."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise TestingLLMError(f"LLM response is not valid JSON: {exc}") from exc

    scope_data = data.get("test_scope", {})
    test_scope = TestScope(
        in_scope=scope_data.get("in_scope", []),
        out_of_scope=scope_data.get("out_of_scope", []),
    )

    regression_tests = [
        RegressionTest(
            component=r.get("component", ""),
            description=r.get("description", ""),
            priority=r.get("priority", ""),
            automated=bool(r.get("automated", False)),
        )
        for r in data.get("regression_tests", [])
    ]

    integration_tests = [
        IntegrationTest(
            source_component=t.get("source_component", ""),
            target_component=t.get("target_component", ""),
            relationship=t.get("relationship", ""),
            description=t.get("description", ""),
            priority=t.get("priority", ""),
        )
        for t in data.get("integration_tests", [])
    ]

    edge_cases = [
        EdgeCase(
            description=e.get("description", ""),
            component=e.get("component", ""),
            severity=e.get("severity", ""),
            category=e.get("category", ""),
        )
        for e in data.get("edge_cases", [])
    ]

    environments = [
        EnvironmentRequirement(
            name=env.get("name", ""),
            description=env.get("description", ""),
            services_required=env.get("services_required", []),
        )
        for env in data.get("environment_requirements", [])
    ]

    execution_order = [
        ExecutionPhase(
            order=p.get("order", i + 1),
            title=p.get("title", ""),
            description=p.get("description", ""),
            test_types=p.get("test_types", []),
            depends_on_phases=p.get("depends_on_phases", []),
        )
        for i, p in enumerate(data.get("execution_order", []))
    ]

    automation_candidates = [
        AutomationCandidate(
            description=a.get("description", ""),
            component=a.get("component", ""),
            test_type=a.get("test_type", ""),
            reason=a.get("reason", ""),
        )
        for a in data.get("automation_candidates", [])
    ]

    manual_validations = [
        ManualValidation(
            description=m.get("description", ""),
            component=m.get("component", ""),
            reason=m.get("reason", ""),
        )
        for m in data.get("manual_validations", [])
    ]

    risks = [
        TestRisk(
            description=r.get("description", ""),
            severity=r.get("severity", ""),
            affected_component=r.get("affected_component", ""),
            mitigation=r.get("mitigation", ""),
        )
        for r in data.get("risks", [])
    ]

    return TestPlan(
        goal=goal,
        executive_summary=data.get("executive_summary", ""),
        test_scope=test_scope,
        affected_repositories=data.get("affected_repositories", []),
        affected_components=data.get("affected_components", []),
        regression_tests=regression_tests,
        integration_tests=integration_tests,
        edge_cases=edge_cases,
        environment_requirements=environments,
        execution_order=execution_order,
        automation_candidates=automation_candidates,
        manual_validations=manual_validations,
        risks=risks,
        recommendations=data.get("recommendations", []),
        graph_context_used=bool(data.get("graph_context_used", False)),
        repositories_consulted=[],
        prompt_version=_PROMPT_VERSION,
    )


# ---------------------------------------------------------------------------
# Test Planning Agent
# ---------------------------------------------------------------------------


class TestPlanningAgent:
    """Implements IAgent for goal=plan_tests.

    Stateless singleton — db session and Neo4j driver are resolved per-run
    from context.extras["db"] and get_driver().
    """

    async def run(self, context: AgentContext) -> AgentOutput:
        task_description: str = context.subject.display_name
        subject_id: str = context.subject.subject_id

        logger.info(
            "testing_agent_started subject_id=%s task=%.80s model=%s",
            subject_id, task_description, context.model,
        )

        db: AsyncSession = context.extras["db"]
        driver = get_driver()
        graph_repo = Neo4jGraphRepository(driver)

        evidence: list[Evidence] = []

        # ------------------------------------------------------------------
        # Step 0 — Read the Planning and Development stages' full results,
        # when this run is part of a workflow (context.extras["workflow"] is
        # only set there; see workflows.py's schedule_run_execution calls).
        # Untruncated — see module docstring for why that matters; this is
        # what previously left the test plan with no idea which components
        # Development had actually changed.
        # ------------------------------------------------------------------
        workflow = context.extras.get("workflow")
        planning_result = get_stage_result(workflow, "planning") if workflow else None
        development_result = get_stage_result(workflow, "development") if workflow else None
        prior_blocks = [
            format_planning_block(planning_result) if planning_result else None,
            format_development_block(development_result) if development_result else None,
        ]
        prior_stage_context = "\n\n".join(b for b in prior_blocks if b)
        if workflow is not None:
            found = [
                label
                for label, result in (("Planning", planning_result), ("Development", development_result))
                if result is not None
            ]
            evidence.append(
                Evidence(
                    kind="tool_call",
                    reference="read_prior_stage_context",
                    summary=(
                        f"Read the full {' and '.join(found)} stage result(s) via get_stage_result()."
                        if found
                        else "No completed prior stage results were available to read."
                    ),
                )
            )

        # ------------------------------------------------------------------
        # Step 1 — Discover indexed repositories (tool_call evidence)
        # ------------------------------------------------------------------
        repos_tool = TestRepositoryDiscoveryTool(db=db, graph_repository=graph_repo)
        repos_obs = await repos_tool.execute()
        evidence.append(to_evidence(repos_obs, "tool_call"))

        indexed_repos: list[dict[str, str]] = repos_obs.data.get("indexed_repositories", [])
        logger.info("testing_agent_step1 indexed_repo_count=%d", len(indexed_repos))

        # ------------------------------------------------------------------
        # Step 2 — Discover components (graph_traversal evidence)
        # ------------------------------------------------------------------
        components_tool = TestComponentDiscoveryTool(graph_repository=graph_repo)
        components_obs = await components_tool.execute(indexed_repos)
        evidence.append(to_evidence(components_obs, "graph_traversal"))

        component_count = len(components_obs.data.get("components", []))
        topic_count = len(components_obs.data.get("kafka_topics", []))
        logger.info(
            "testing_agent_step2 component_count=%d topic_count=%d",
            component_count, topic_count,
        )

        # ------------------------------------------------------------------
        # Step 3 — Traverse dependencies for integration points (graph_traversal)
        # ------------------------------------------------------------------
        deps_tool = TestDependencyTraversalTool(graph_repository=graph_repo)
        deps_obs = await deps_tool.execute(indexed_repos)
        evidence.append(to_evidence(deps_obs, "graph_traversal"))

        edge_count = deps_obs.data.get("total_edges", 0)
        integration_count = len(deps_obs.data.get("integration_points", []))
        cross_repo_count = len(deps_obs.data.get("cross_repo_edges", []))
        logger.info(
            "testing_agent_step3 edge_count=%d integration_points=%d cross_repo=%d",
            edge_count, integration_count, cross_repo_count,
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
            if integration_count > 0:
                base_confidence = 0.88
            if cross_repo_count > 0:
                base_confidence = 0.92
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
            "testing_agent_synthesizing has_graph_data=%s graph_context_chars=%d",
            has_graph_data, len(graph_context_text),
        )

        try:
            raw_response = await _call_llm(user_prompt=prompt, model=context.model)
            test_plan = _parse_llm_response(raw_response, task_description)
        except TestingLLMError as exc:
            logger.error("testing_agent_llm_failed error=%s", str(exc))
            raise

        # Back-fill repositories_consulted
        test_plan.repositories_consulted = [r["name"] for r in indexed_repos]

        # Never trust the LLM's self-reported graph_context_used — derive it
        # from what the tools actually returned.
        test_plan.graph_context_used = has_graph_data

        # ------------------------------------------------------------------
        # Verify claims against this run's own tool evidence (see
        # app.agents.verification) — generic string membership, no
        # source-code parsing. Also flags, unconditionally, that this
        # agent never executes anything: it is a test plan, not a test
        # result, and nothing downstream should mistake one for the other.
        # ------------------------------------------------------------------
        evidence_pool = verification.build_evidence_pool(
            [r["name"] for r in indexed_repos],
            [c.get("name", "") for c in components_obs.data.get("components", [])],
            [c.get("file_path", "") for c in components_obs.data.get("components", [])],
            [t.get("name", "") for t in components_obs.data.get("kafka_topics", [])],
        )
        verification_warnings: list[str] = [
            "This is a test PLAN produced by an LLM — no test in it has actually "
            "been executed. Regression/integration/edge-case results below are "
            "proposed coverage, not verified pass/fail outcomes."
        ]
        repo_check = verification.verify_claims(test_plan.affected_repositories, evidence_pool)
        for name in repo_check.unverified:
            verification_warnings.append(
                f"Repository '{name}' cited in this test plan was not found among the "
                "repositories this run's graph traversal actually returned — unverified."
            )
        component_claims = list(test_plan.affected_components)
        component_claims += [t.component for t in test_plan.regression_tests]
        component_claims += [t.source_component for t in test_plan.integration_tests]
        component_claims += [t.target_component for t in test_plan.integration_tests]
        comp_check = verification.verify_claims(component_claims, evidence_pool)
        for name in comp_check.unverified:
            verification_warnings.append(
                f"Component '{name}' referenced in this test plan does not appear in "
                "this run's indexed graph data — unverified."
            )
        test_plan.verification_warnings = verification_warnings
        evidence.append(
            Evidence(
                kind="tool_call",
                reference="claim_verification",
                summary=(
                    f"Test plan verification: {len(verification_warnings) - 1} claim(s) "
                    "unverified against this run's own tool evidence (plan-vs-execution "
                    "distinction always noted)."
                ),
            )
        )

        # LLM synthesis evidence
        evidence.append(
            Evidence(
                kind="llm_reasoning",
                reference="llm_synthesis",
                summary=(
                    f"LLM produced test plan with "
                    f"{len(test_plan.regression_tests)} regression test(s), "
                    f"{len(test_plan.integration_tests)} integration test(s), "
                    f"and {len(test_plan.edge_cases)} edge case(s) for: "
                    f"{task_description[:60]}"
                ),
            )
        )

        # ------------------------------------------------------------------
        # Confidence scoring
        # ------------------------------------------------------------------
        confidence_score = base_confidence
        if test_plan.regression_tests or test_plan.integration_tests:
            confidence_score = min(confidence_score + 0.05, 1.0)

        if graph_unavailable:
            confidence_reasoning = (
                "Knowledge Graph was unavailable (infrastructure error); "
                "test plan is based on general QA practices only. "
                "Retry when the graph service is restored."
            )
        elif has_graph_data:
            kafka_clause = f", {topic_count} Kafka topic(s)," if topic_count else ""
            confidence_reasoning = (
                f"Graph traversal found {component_count} component(s){kafka_clause} "
                f"and {integration_count} "
                f"integration point(s) across {len(indexed_repos)} indexed "
                f"repositor{'y' if len(indexed_repos) == 1 else 'ies'}. "
            )
            if cross_repo_count > 0:
                confidence_reasoning += (
                    f"Identified {cross_repo_count} cross-repository coupling(s) "
                    "requiring integration tests. "
                )
            confidence_reasoning += "Test plan is grounded in real architecture data."
        else:
            confidence_reasoning = (
                f"Graph is healthy but contains no architecture data "
                f"({len(indexed_repos)} indexed "
                f"repositor{'y' if len(indexed_repos) == 1 else 'ies'}, "
                f"0 components, 0 edges). "
                "Test plan uses general QA practices."
            )

        logger.info(
            "testing_agent_completed subject_id=%s confidence=%.2f "
            "evidence_count=%d regression=%d integration=%d edge_cases=%d",
            subject_id, confidence_score, len(evidence),
            len(test_plan.regression_tests), len(test_plan.integration_tests),
            len(test_plan.edge_cases),
        )

        return AgentOutput(
            agent_id="testing",
            subject_id=subject_id,
            confidence=Confidence(
                score=confidence_score,
                reasoning=confidence_reasoning,
            ),
            evidence=evidence,
            result=test_plan.model_dump(),
            prompt_version=_PROMPT_VERSION,
        )
