"""Test Planning Agent — Testing Strategy capability.

Implements the IAgent protocol for goal=plan_tests. Every run:
1. Reads repositories and components the Context Discovery stage already
   discovered, via get_stage_result() — see the Context Discovery /
   Context Explorer architecture review's "do not rediscover repositories
   or components" principle. Falls back to TestRepositoryDiscoveryTool/
   TestComponentDiscoveryTool (this agent's own tools) only for a
   standalone run with no context_discovery stage to read.
2. Calls TestDependencyTraversalTool to map integration points
   (graph_traversal evidence). Kept as this agent's own call rather than
   read from Development's `dependencies` field: Development's field is
   an LLM-curated subset shaped for architecture narration, not the raw
   edge/integration-point traversal Testing's own verification checks
   LLM claims against — reusing it would mean verifying the LLM's test
   plan against another LLM's earlier output instead of live graph
   truth, which is a real regression in verification quality, not a
   harmless dedup. This is genuinely incremental, QA-specific analysis,
   the same way Development's own dependency traversal is incremental
   relative to Context Discovery.
3. Synthesizes a structured test plan using the LLM, grounded in the
   graph context gathered above (llm_reasoning evidence).

The agent thinks like a Senior QA Lead: given what changed (Development)
and what exists (Context Discovery), what must be validated? Who depends
on it? What could break? Which interfaces require integration tests?
What edge cases are highest risk? It does not rediscover repositories or
components already discovered upstream.

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

import logging
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import verification
from app.agents._contract import (
    AgentContext,
    AgentOutput,
    Confidence,
    Evidence,
)
from app.agents.component_grounding import check_test_used_as_production, to_contract_warnings
from app.agents.git_ops._artifact_reader import get_stage_result
from app.agents.llm import STAGE_TESTING, invoke_llm_json, stage_for
from app.agents.planning.classifier import extract_key_terms
from app.agents.prompt_utils import parse_json_response, render_prompt_template
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
    TestingObservation,
    TestRailCoverageTool,
    TestRepositoryDiscoveryTool,
    format_graph_context,
    to_evidence,
)
from app.context_pipeline.reasoning.curation import EvidencePackage, render_evidence_package_text
from app.core.exceptions import AppError
from app.graph.neo4j_repository import Neo4jGraphRepository
from app.graph.session import get_driver
from app.graph.test_case_repository import Neo4jTestCaseGraphRepository

logger = logging.getLogger(__name__)

_PROMPT_VERSION = "1.2"
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


async def _call_llm(
    user_prompt: str,
    model: str | None = None,
    stage: str = STAGE_TESTING,
    _metadata_out: dict[str, Any] | None = None,
    context: AgentContext | None = None,
) -> str:
    """Send a single JSON-mode completion through the configured AI
    provider and return the raw content string. Delegates to the shared
    `app.agents.llm.invoke_llm_json` — kept as a module-level function so
    existing test seams (`patch("...agent._call_llm", ...)`) stay stable."""
    return await invoke_llm_json(
        system_prompt=_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        stage=stage,
        model=model,
        error_cls=TestingLLMError,
        metadata_out=_metadata_out,
        context=context,
    )


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
    data = parse_json_response(raw, TestingLLMError)

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
            subject_id,
            task_description,
            context.model,
        )

        db: AsyncSession = context.extras["db"]
        # Prefer the hop-budgeted repository RunCoordinator's Context
        # Preparation step builds from this agent's own manifest
        # (max_graph_hops=3 — see manifest.py); construct a plain,
        # unbudgeted one only when running outside that dispatcher (e.g.
        # a unit test calling `.run()` directly).
        graph_repo = context.extras.get("graph_repository") or Neo4jGraphRepository(get_driver())
        # Same injectable-with-a-real-default pattern as graph_repo above —
        # lets a unit test substitute a fake without touching a real Neo4j
        # (this codebase's unit tests are I/O-free by convention).
        test_case_graph_repo = context.extras.get(
            "test_case_graph_repository"
        ) or Neo4jTestCaseGraphRepository(get_driver())

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
                for label, result in (
                    ("Planning", planning_result),
                    ("Development", development_result),
                )
                if result is not None
            ]
            evidence.append(
                Evidence(
                    kind="tool_call",
                    reference="read_prior_stage_context",
                    summary=(
                        f"Read the full {' and '.join(found)} stage result(s) "
                        "via get_stage_result()."
                        if found
                        else "No completed prior stage results were available to read."
                    ),
                )
            )

        # ------------------------------------------------------------------
        # Steps 1-2 — Repositories and components. Read from the Context
        # Discovery stage when this run is part of a workflow (see module
        # docstring); wrapped as a TestingObservation so
        # format_graph_context()/to_evidence() below work unchanged. Falls
        # back to this agent's own tools only for a standalone run.
        # ------------------------------------------------------------------
        context_discovery_result = (
            get_stage_result(workflow, "context_discovery") if workflow else None
        )

        if context_discovery_result is not None:
            indexed_repos_raw = context_discovery_result.get("indexed_repositories") or []
            repos_obs = TestingObservation(
                tool_name="context_discovery_repositories",
                summary=(
                    f"Reused {len(indexed_repos_raw)} indexed repository(ies) already "
                    "discovered by the Context Discovery stage."
                ),
                data={"indexed_repositories": indexed_repos_raw},
            )
            components_raw = context_discovery_result.get("graph_components") or []
            topics_raw = context_discovery_result.get("graph_topics") or []
            components_obs = TestingObservation(
                tool_name="context_discovery_components",
                summary=(
                    f"Reused {len(components_raw)} component(s) and {len(topics_raw)} "
                    "topic(s) already discovered by the Context Discovery stage."
                ),
                data={"components": components_raw, "kafka_topics": topics_raw},
            )
            evidence.append(
                Evidence(
                    kind="tool_call",
                    reference="read_context_discovery_stage",
                    summary=(
                        "Read repositories and components from the Context Discovery "
                        "stage's result via get_stage_result() — see that stage's own "
                        "Evidence tab for the full discovery trail."
                    ),
                )
            )
        else:
            repos_tool = TestRepositoryDiscoveryTool(db=db, graph_repository=graph_repo)
            repos_obs = await repos_tool.execute()
            evidence.append(to_evidence(repos_obs, "tool_call"))

            components_tool = TestComponentDiscoveryTool(graph_repository=graph_repo)
            components_obs = await components_tool.execute(
                repos_obs.data.get("indexed_repositories", [])
            )
            evidence.append(to_evidence(components_obs, "graph_traversal"))

        indexed_repos: list[dict[str, str]] = repos_obs.data.get("indexed_repositories", [])
        component_count = len(components_obs.data.get("components", []))
        topic_count = len(components_obs.data.get("kafka_topics", []))
        logger.info(
            "testing_agent_repos_and_components indexed_repo_count=%d "
            "component_count=%d topic_count=%d source=%s",
            len(indexed_repos),
            component_count,
            topic_count,
            "context_discovery" if context_discovery_result is not None else "own_tools",
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
            edge_count,
            integration_count,
            cross_repo_count,
        )

        # ------------------------------------------------------------------
        # Step 4 — Existing TestRail/uploaded test coverage relevant to this
        # task. Supplementary, not gating: coverage existing or not never
        # affects confidence below, only whether the prompt gets an extra
        # "here's what's already covered" section.
        #
        # Same "prefer the context_discovery stage's result, fall back to
        # this agent's own tool for a standalone run" pattern as
        # repositories/components above (Steps 1-2) — context_discovery's
        # TestCoverageInvestigator already ran this exact relevance search
        # once per workflow; re-running it here would just be a second live
        # Neo4j query for data already sitting in the stage result.
        # ------------------------------------------------------------------
        if context_discovery_result is not None:
            existing_cases = context_discovery_result.get("existing_test_coverage") or []
            testrail_obs = TestingObservation(
                tool_name="context_discovery_test_coverage",
                summary=(
                    f"Reused {len(existing_cases)} relevant test case(s) already found by the "
                    "Context Discovery stage."
                    if existing_cases
                    else "Context Discovery found no existing test coverage relevant to this "
                    "task."
                ),
                data={"cases": existing_cases, "total_synced": len(existing_cases)},
            )
            evidence.append(
                Evidence(
                    kind="tool_call",
                    reference="read_context_discovery_test_coverage",
                    summary="Read existing TestRail/uploaded test coverage from the Context "
                    "Discovery stage's result via get_stage_result().",
                )
            )
        else:
            testrail_terms = list(extract_key_terms(task_description))
            testrail_tool = TestRailCoverageTool(test_case_graph_repository=test_case_graph_repo)
            testrail_obs = await testrail_tool.execute(testrail_terms)
            evidence.append(to_evidence(testrail_obs, "graph_traversal"))

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
        # Prefer the curated Evidence Package (see reasoning.curation) when
        # Context Discovery produced one — see development/agent.py's
        # identical comment for the full rationale. It already folds in
        # test-coverage evidence generically (curation reads the same
        # ledger TestCoverageInvestigator writes to), so only the fallback
        # path below needs testrail_obs passed explicitly.
        evidence_package = (context_discovery_result or {}).get("evidence_package") or {}
        if evidence_package.get("items"):
            graph_context_text = render_evidence_package_text(
                EvidencePackage.model_validate(evidence_package)
            )
        else:
            graph_context_text = format_graph_context(
                repos_obs, components_obs, deps_obs, testrail_obs
            )
        prompt_task_description = (
            f"{task_description}\n\n{prior_stage_context}"
            if prior_stage_context
            else task_description
        )
        prompt = _render_prompt(prompt_task_description, graph_context_text)

        logger.info(
            "testing_agent_synthesizing has_graph_data=%s graph_context_chars=%d",
            has_graph_data,
            len(graph_context_text),
        )

        try:
            llm_metadata: dict[str, Any] = {}
            raw_response = await _call_llm(
                user_prompt=prompt,
                model=context.model,
                stage=stage_for(context.extras, STAGE_TESTING),
                _metadata_out=llm_metadata,
                context=context,
            )
            test_plan = _parse_llm_response(raw_response, task_description)
        except TestingLLMError as exc:
            logger.error("testing_agent_llm_failed error=%s", str(exc))
            raise

        # Back-fill repositories_consulted — canonical "owner/name" identity,
        # same rationale as planning/development (see
        # `TestRepositoryDiscoveryTool`'s docstring in testing/tools.py).
        # `.get(...)` falls back to the bare name for pre-fix persisted data.
        test_plan.repositories_consulted = [r.get("full_name") or r["name"] for r in indexed_repos]

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
            [r.get("full_name") or r["name"] for r in indexed_repos],
            [c.get("name", "") for c in components_obs.data.get("components", [])],
            [c.get("file_path", "") for c in components_obs.data.get("components", [])],
            [t.get("name", "") for t in components_obs.data.get("kafka_topics", [])],
        )
        # `execution_status_note` (schemas.py) carries the "this is a plan,
        # not an execution" disclaimer instead of this list — see that
        # field's docstring. verification_warnings/verification_findings
        # below are reserved for actual claim-vs-evidence checks, never a
        # true-on-every-run statement of fact.
        verification_warnings: list[str] = []
        verification_findings: list[verification.VerificationFinding] = []

        def _warn(message: str, category: str) -> None:
            verification_warnings.append(message)
            verification_findings.append(
                verification.VerificationFinding(message=message, category=category)
            )

        repo_check = verification.verify_claims(test_plan.affected_repositories, evidence_pool)
        for name in repo_check.unverified:
            _warn(
                f"Repository '{name}' cited in this test plan was not found among the "
                "repositories this run's graph traversal actually returned — unverified.",
                "repository_not_found",
            )

        # ------------------------------------------------------------------
        # Independent grounding (see app.agents.component_grounding): this
        # agent's OWN re-check against the graph_components it just read
        # above — a test plan that repeats an earlier stage's test-class-
        # as-production mistake (a real run's regression/integration tests
        # were built entirely around `TestSCDType2Merger`/
        # `TestExactDeduplicator`) is caught here independently, not just
        # inherited from Planning/Development's own warnings.
        # ------------------------------------------------------------------
        graph_components_list = components_obs.data.get("components", [])
        test_plan_claims = list(test_plan.affected_components)
        test_plan_claims += [t.component for t in test_plan.regression_tests]
        test_plan_claims += [t.source_component for t in test_plan.integration_tests]
        test_plan_claims += [t.target_component for t in test_plan.integration_tests]
        _, component_warnings = check_test_used_as_production(
            test_plan_claims, graph_components_list, task_description
        )
        corrections = {w.claim: w.suggested_replacement for w in component_warnings}
        if corrections:
            corrected_components = [
                corrections.get(name, name) for name in test_plan.affected_components
            ]
            test_plan.affected_components = [c for c in corrected_components if c is not None]
            kept_regression_tests = []
            for rt in test_plan.regression_tests:
                if rt.component in corrections:
                    replacement = corrections[rt.component]
                    if replacement is None:
                        continue  # rejected — no production sibling found
                    rt.component = replacement
                kept_regression_tests.append(rt)
            test_plan.regression_tests = kept_regression_tests
            kept_integration_tests = []
            for it in test_plan.integration_tests:
                source_rejected = (
                    it.source_component in corrections and corrections[it.source_component] is None
                )
                target_rejected = (
                    it.target_component in corrections and corrections[it.target_component] is None
                )
                if source_rejected or target_rejected:
                    continue  # can't test a CALLS relationship with no real endpoint
                if it.source_component in corrections:
                    it.source_component = corrections[it.source_component]  # type: ignore[assignment]
                if it.target_component in corrections:
                    it.target_component = corrections[it.target_component]  # type: ignore[assignment]
                kept_integration_tests.append(it)
            test_plan.integration_tests = kept_integration_tests
        if component_warnings:
            for w in component_warnings:
                _warn(w.message, "component_misattribution")
            evidence.append(
                Evidence(
                    kind="tool_call",
                    reference="component_grounding",
                    summary=(
                        f"{len(component_warnings)} component(s) referenced in this test "
                        "plan named a test class instead of the production code it tests "
                        "— independently re-checked against this run's own indexed graph "
                        "data: " + "; ".join(w.message for w in component_warnings)
                    ),
                )
            )

        # Deduplicated: the same component name commonly appears in more
        # than one of affected_components/regression_tests/integration_tests
        # (e.g. as both a regression test's component and an integration
        # test's source_component) — checking the raw, repeated list
        # against evidence produced one identical "unverified" warning per
        # occurrence rather than per distinct claim.
        unique_component_claims = list(dict.fromkeys(c for c in test_plan_claims if c))
        comp_check = verification.verify_claims(unique_component_claims, evidence_pool)
        for name in comp_check.unverified:
            _warn(
                f"Component '{name}' referenced in this test plan does not appear in "
                "this run's indexed graph data — unverified.",
                "component_not_found",
            )
        test_plan.verification_warnings = verification_warnings
        test_plan.verification_findings = [f.to_dict() for f in verification_findings]
        test_plan.component_warnings = to_contract_warnings(component_warnings)
        evidence.append(
            Evidence(
                kind="tool_call",
                reference="claim_verification",
                summary=(
                    f"Test plan verification: {len(verification_warnings)} claim(s) "
                    "unverified against this run's own tool evidence. This is a "
                    "PLAN, not an execution — see execution_status_note."
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
            subject_id,
            confidence_score,
            len(evidence),
            len(test_plan.regression_tests),
            len(test_plan.integration_tests),
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
