"""Unit tests for the Engineering Review Agent.

Unlike Planning/Development/Testing, this agent runs no graph tools —
only the LLM call is mocked. Covers: happy path evidence/result shape,
readiness-status-driven confidence, LLM failure, manifest, registration,
selector routing, and — the actual bug this agent's context-building
exists to fix — that it reads full structured Planning/Development/
Testing results via get_stage_result() rather than the old, lossy
256-char freetext summary.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.agents._contract import AgentContext, Subject
from app.agents.engineering_review.agent import (
    EngineeringReviewAgent,
    EngineeringReviewLLMError,
)
from app.agents.engineering_review.manifest import ENGINEERING_REVIEW_MANIFEST
from app.agents.engineering_review.schemas import EngineeringReadinessReport

# ---------------------------------------------------------------------------
# Helpers — SimpleNamespace-based workflow/run fakes, matching the pattern
# already established in tests/unit/ai/test_git_ops_agents.py for agents
# that read prior stage results via get_stage_result().
# ---------------------------------------------------------------------------


def _make_step(result: dict | None) -> SimpleNamespace:
    return SimpleNamespace(result=result)


def _make_run(stage: str, status: str, result: dict | None) -> SimpleNamespace:
    return SimpleNamespace(
        workflow_stage=stage,
        status=status,
        steps=[_make_step(result)] if result is not None else [],
        created_at=datetime.now(UTC),
    )


def _make_workflow(runs: list | None = None) -> SimpleNamespace:
    return SimpleNamespace(runs=runs or [])


def _make_context(
    workflow: SimpleNamespace | None = None,
    display_name: str = "Add rate limiting",
) -> AgentContext:
    subject = Subject(
        subject_id="freetext:abc123",
        subject_type="freetext",
        display_name=display_name,
    )
    extras: dict = {"db": AsyncMock()}
    if workflow is not None:
        extras["workflow"] = workflow
    return AgentContext(subject=subject, goal="review_readiness", extras=extras)


def _planning_result() -> dict:
    return {
        "goal": "plan_freeform",
        "executive_summary": "Add a token-bucket rate limiter to the payment API.",
        "implementation_steps": [
            {
                "order": 1,
                "description": "Add RateLimiterService",
                "affected_component": "PaymentController",
                "risk_note": "Config drift across environments.",
            },
        ],
        "affected_components": ["PaymentController"],
        "kafka_topics_involved": ["payment-events"],
        "risk_considerations": ["Rate limit config drift"],
        "repositories_consulted": ["payment-service"],
    }


def _development_result() -> dict:
    """Uses the DevelopmentPlan schema's real field names — `repositories`
    and `components`, not `affected_repositories`/`affected_components` —
    the exact mismatch that used to make Development's picks vanish."""
    return {
        "goal": "develop_change_plan",
        "executive_summary": "Introduce a token-bucket limiter in front of PaymentController.",
        "repositories": [
            {"name": "payment-service", "owner": "acme", "reason": "Hosts PaymentController."},
        ],
        "components": [
            {
                "name": "RateLimiterService",
                "component_type": "Service",
                "repository": "payment-service",
                "file_path": "src/main/RateLimiterService.java",
                "change_description": "New token-bucket limiter service.",
            },
        ],
        "dependencies": [
            {
                "source": "PaymentController",
                "target": "RateLimiterService",
                "relationship": "CALLS",
                "risk_note": "",
            },
        ],
        "reusable_implementations": [],
        "implementation_phases": [
            {
                "order": 1,
                "title": "Add RateLimiterService",
                "description": "Introduce the limiter and wire it into PaymentController.",
                "affected_components": ["PaymentController"],
                "estimated_complexity": "medium",
                "depends_on_phases": [],
            },
        ],
        "risks": [
            {
                "description": "Rate limit config drift",
                "severity": "medium",
                "affected_component": "RateLimiterService",
                "mitigation": "Centralize config in a shared ConfigMap.",
            },
        ],
        "recommendations": ["Add a load test before rollout."],
    }


def _testing_result() -> dict:
    return {
        "goal": "plan_tests",
        "executive_summary": "Regression and integration coverage for the new limiter.",
        "test_scope": {"in_scope": ["PaymentController"], "out_of_scope": ["Billing"]},
        "affected_repositories": ["payment-service"],
        "affected_components": ["PaymentController"],
        "regression_tests": [
            {
                "component": "PaymentController",
                "description": "Requests over the limit are rejected with 429.",
                "priority": "high",
                "automated": True,
            },
        ],
        "integration_tests": [],
        "edge_cases": [],
        "environment_requirements": [],
        "execution_order": [],
        "automation_candidates": [],
        "manual_validations": [],
        "risks": [],
        "recommendations": [],
    }


def _make_llm_response(
    readiness_status: str = "ready", blocking_issues: list[str] | None = None
) -> str:
    return json.dumps(
        {
            "executive_summary": "The blueprint is complete and internally consistent.",
            "readiness_status": readiness_status,
            "completeness_findings": [
                {
                    "area": "Implementation Steps",
                    "status": "complete",
                    "detail": "All steps map to real components.",
                },
            ],
            "repository_review": ["payment-service is correctly scoped for this change."],
            "component_review": ["PaymentController is the right entry point."],
            "risk_assessment": [
                {
                    "description": "Rate limit config drift",
                    "adequately_mitigated": True,
                    "concern": "",
                },
            ],
            "dependency_assessment": [
                {
                    "description": "PaymentController -> RateLimiterService",
                    "validated": True,
                    "concern": "",
                },
            ],
            "test_strategy_review": ["Regression tests cover the modified endpoint."],
            "blocking_issues": blocking_issues or [],
            "recommendations": ["Add a load test before rollout."],
        }
    )


def _documentation_planning_result() -> dict:
    return {
        "goal": "plan_documentation",
        "executive_summary": "README and a new runbook are needed for the rate limiter.",
        "documentation_impact": "medium",
        "impact_explanation": "New service, no user-facing API change.",
        "required_updates": [
            {
                "document": "README.md",
                "category": "repository",
                "current_status": "",
                "action": "update",
                "reason": "New RateLimiterService is not mentioned.",
                "priority": "medium",
                "owner": "Backend maintainer",
                "estimated_effort": "small",
                "dependencies": [],
            },
        ],
        "new_documentation": [],
        "existing_updates": [],
        "risks": [],
        "recommendations": [],
        "release_notes_draft": [],
        "checklist": [],
    }


def _full_workflow() -> SimpleNamespace:
    return _make_workflow(
        [
            _make_run("planning", "completed", _planning_result()),
            _make_run("development", "completed", _development_result()),
            _make_run("testing", "completed", _testing_result()),
            _make_run(
                "documentation_planning", "completed", _documentation_planning_result()
            ),
        ]
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_engineering_review_agent_happy_path() -> None:
    context = _make_context(workflow=_full_workflow())

    with patch(
        "app.agents.engineering_review.agent._call_llm",
        new=AsyncMock(return_value=_make_llm_response()),
    ):
        agent = EngineeringReviewAgent()
        output = await agent.run(context)

    evidence_kinds = {e.kind for e in output.evidence}
    assert "tool_call" in evidence_kinds
    assert "llm_reasoning" in evidence_kinds

    assert output.result["executive_summary"]
    assert output.result["readiness_status"] == "ready"
    assert len(output.result["completeness_findings"]) == 1
    assert len(output.result["risk_assessment"]) == 1
    assert len(output.result["dependency_assessment"]) == 1
    assert output.result["blocking_issues"] == []
    # Regression: `goal` used to be set to the entire blueprint context
    # string instead of the real goal.
    assert output.result["goal"] == "review_readiness"

    assert output.agent_id == "engineering_review"
    assert output.subject_id == "freetext:abc123"
    assert output.prompt_version == "1.0"
    assert "graph_context_used" not in output.result


@pytest.mark.asyncio
async def test_engineering_review_agent_confidence_tracks_readiness_status() -> None:
    context = _make_context(workflow=_full_workflow())

    with patch(
        "app.agents.engineering_review.agent._call_llm",
        new=AsyncMock(
            return_value=_make_llm_response(
                readiness_status="not_ready",
                blocking_issues=["Missing test coverage for the new endpoint."],
            )
        ),
    ):
        agent = EngineeringReviewAgent()
        output = await agent.run(context)

    assert output.result["readiness_status"] == "not_ready"
    assert output.result["blocking_issues"] == ["Missing test coverage for the new endpoint."]
    assert output.confidence.score <= 0.4


@pytest.mark.asyncio
async def test_engineering_review_agent_confidence_penalized_by_verification_warnings() -> None:
    """Two runs both landing on the same readiness verdict must not read as
    equally confident if one carries forward real, deterministic
    verification warnings and the other doesn't — the categorical
    downgrade below protects the dangerous case (a false "ready"), but the
    numeric score also reflects warning volume."""
    planning_with_warnings = _planning_result()
    planning_with_warnings["verification_warnings"] = [
        "Repository 'billing-service' cited in this plan was not found among the "
        "repositories this run's graph traversal actually returned — unverified.",
        "Component 'PaymentValidator' cited was not found — unverified.",
    ]
    workflow = _make_workflow(
        [
            _make_run("planning", "completed", planning_with_warnings),
            _make_run("development", "completed", _development_result()),
            _make_run("testing", "completed", _testing_result()),
            _make_run("documentation_planning", "completed", _documentation_planning_result()),
        ]
    )
    context = _make_context(workflow=workflow)

    with patch(
        "app.agents.engineering_review.agent._call_llm",
        new=AsyncMock(return_value=_make_llm_response(readiness_status="needs_revision")),
    ):
        output = await EngineeringReviewAgent().run(context)

    assert output.result["readiness_status"] == "needs_revision"
    # base 0.6 - min(0.2, 0.05*2) = 0.5
    assert output.confidence.score == 0.5
    assert (
        "carried-forward deterministic blocking verification warning" in output.confidence.reasoning
    )


# ---------------------------------------------------------------------------
# Structured warning classification — the readiness guardrail must key off
# `VerificationFinding.blocking`, never off "any prior finding is present".
# Root cause: Testing's own "this is a test PLAN, not an execution"
# disclaimer used to live in `verification_warnings` unconditionally, on
# every single run, which made `readiness_status: "ready"` permanently
# unreachable for any workflow — this section locks in the fix.
# ---------------------------------------------------------------------------


def _finding(message: str, category: str) -> dict[str, str | bool]:
    from app.agents.verification import NON_BLOCKING_CATEGORIES

    return {
        "message": message,
        "category": category,
        "blocking": category not in NON_BLOCKING_CATEGORIES,
    }


@pytest.mark.asyncio
async def test_engineering_review_agent_ready_when_no_findings_at_all() -> None:
    """C1: nothing flagged by any stage — "ready" must remain reachable."""
    context = _make_context(workflow=_full_workflow())

    with patch(
        "app.agents.engineering_review.agent._call_llm",
        new=AsyncMock(return_value=_make_llm_response(readiness_status="ready")),
    ):
        output = await EngineeringReviewAgent().run(context)

    assert output.result["readiness_status"] == "ready"
    assert output.result["blocking_verification_warnings"] == []


@pytest.mark.asyncio
async def test_engineering_review_agent_ready_when_only_informational_findings() -> None:
    """C2: an informational-only finding (e.g. Testing's plan-vs-execution
    disclaimer, classified `category="informational"`) must NOT force a
    downgrade — "ready" must still be reachable. This is the exact bug: an
    always-present informational note made "ready" unreachable for every
    workflow before this classification existed."""
    testing_with_note = _testing_result()
    testing_with_note["verification_findings"] = [
        _finding(
            "This is a test PLAN produced by an LLM — no test in it has "
            "actually been executed.",
            "informational",
        )
    ]
    workflow = _make_workflow(
        [
            _make_run("planning", "completed", _planning_result()),
            _make_run("development", "completed", _development_result()),
            _make_run("testing", "completed", testing_with_note),
            _make_run("documentation_planning", "completed", _documentation_planning_result()),
        ]
    )
    context = _make_context(workflow=workflow)

    with patch(
        "app.agents.engineering_review.agent._call_llm",
        new=AsyncMock(return_value=_make_llm_response(readiness_status="ready")),
    ):
        output = await EngineeringReviewAgent().run(context)

    assert output.result["readiness_status"] == "ready"
    assert output.result["blocking_verification_warnings"] == []
    # Still visible for a human reviewer, just not blocking.
    assert any("test PLAN" in w for w in output.result["prior_verification_warnings"])


@pytest.mark.asyncio
async def test_engineering_review_agent_downgrades_on_genuine_repository_warning() -> None:
    """C3: a real, classified `repository_not_found` finding must still
    force a downgrade even when combined with informational noise."""
    planning_with_findings = _planning_result()
    planning_with_findings["verification_findings"] = [
        _finding(
            "Repository 'billing-service' cited in this plan was not found "
            "among the repositories this run's graph traversal actually "
            "returned — unverified.",
            "repository_not_found",
        )
    ]
    testing_with_note = _testing_result()
    testing_with_note["verification_findings"] = [
        _finding("This is a test PLAN, not an execution.", "informational")
    ]
    workflow = _make_workflow(
        [
            _make_run("planning", "completed", planning_with_findings),
            _make_run("development", "completed", _development_result()),
            _make_run("testing", "completed", testing_with_note),
            _make_run("documentation_planning", "completed", _documentation_planning_result()),
        ]
    )
    context = _make_context(workflow=workflow)

    with patch(
        "app.agents.engineering_review.agent._call_llm",
        new=AsyncMock(return_value=_make_llm_response(readiness_status="ready")),
    ):
        output = await EngineeringReviewAgent().run(context)

    assert output.result["readiness_status"] == "needs_revision"
    assert any("billing-service" in w for w in output.result["blocking_verification_warnings"])
    assert not any(
        "test PLAN" in w for w in output.result["blocking_verification_warnings"]
    )


@pytest.mark.asyncio
async def test_engineering_review_agent_downgrades_on_genuine_component_warning() -> None:
    """C4: a real, classified `component_misattribution`/`component_not_found`
    finding must still force a downgrade."""
    development_with_findings = _development_result()
    development_with_findings["verification_findings"] = [
        _finding(
            "File 'src/main/RateLimiterService.java' claimed for "
            "'payment-service' is indexed under 'billing-service' — likely "
            "misattributed to the wrong repository.",
            "component_misattribution",
        )
    ]
    workflow = _make_workflow(
        [
            _make_run("planning", "completed", _planning_result()),
            _make_run("development", "completed", development_with_findings),
            _make_run("testing", "completed", _testing_result()),
            _make_run("documentation_planning", "completed", _documentation_planning_result()),
        ]
    )
    context = _make_context(workflow=workflow)

    with patch(
        "app.agents.engineering_review.agent._call_llm",
        new=AsyncMock(return_value=_make_llm_response(readiness_status="ready")),
    ):
        output = await EngineeringReviewAgent().run(context)

    assert output.result["readiness_status"] == "needs_revision"
    assert any(
        "misattributed" in w for w in output.result["blocking_verification_warnings"]
    )


@pytest.mark.asyncio
async def test_engineering_review_agent_downgrades_on_unknown_category() -> None:
    """C5: fail-closed — a category nobody has classified as non-blocking
    yet must still block, never silently pass. Simulates a future producer
    that introduces a new category without updating
    NON_BLOCKING_CATEGORIES."""
    planning_with_findings = _planning_result()
    planning_with_findings["verification_findings"] = [
        {
            "message": "Some brand-new kind of check flagged something.",
            "category": "some_future_category_nobody_classified",
            "blocking": True,
        }
    ]
    workflow = _make_workflow(
        [
            _make_run("planning", "completed", planning_with_findings),
            _make_run("development", "completed", _development_result()),
            _make_run("testing", "completed", _testing_result()),
            _make_run("documentation_planning", "completed", _documentation_planning_result()),
        ]
    )
    context = _make_context(workflow=workflow)

    with patch(
        "app.agents.engineering_review.agent._call_llm",
        new=AsyncMock(return_value=_make_llm_response(readiness_status="ready")),
    ):
        output = await EngineeringReviewAgent().run(context)

    assert output.result["readiness_status"] == "needs_revision"
    assert output.result["blocking_verification_warnings"] != []


@pytest.mark.asyncio
async def test_engineering_review_agent_legacy_unclassified_warning_still_blocks() -> None:
    """Backward compatibility / fail-closed: a stage result persisted
    before this classification existed (only `verification_warnings`,
    no `verification_findings`) must still block — exactly as it did
    before this fix, never silently dropped from the blocking decision."""
    planning_legacy = _planning_result()
    planning_legacy["verification_warnings"] = [
        "Repository 'billing-service' cited in this plan was not found."
    ]
    # Deliberately no "verification_findings" key — simulates data
    # persisted before this classification existed.
    workflow = _make_workflow(
        [
            _make_run("planning", "completed", planning_legacy),
            _make_run("development", "completed", _development_result()),
            _make_run("testing", "completed", _testing_result()),
            _make_run("documentation_planning", "completed", _documentation_planning_result()),
        ]
    )
    context = _make_context(workflow=workflow)

    with patch(
        "app.agents.engineering_review.agent._call_llm",
        new=AsyncMock(return_value=_make_llm_response(readiness_status="ready")),
    ):
        output = await EngineeringReviewAgent().run(context)

    assert output.result["readiness_status"] == "needs_revision"
    assert output.result["blocking_verification_warnings"] != []


# ---------------------------------------------------------------------------
# Confidence now via the shared engine (app.agents.confidence) — Delta
# Report follow-up: Engineering Review used to compute this with local
# arithmetic instead of calculate_weighted_confidence, the one architectural
# inconsistency left across the three evidence-based agents. These tests
# lock in bit-for-bit equivalence with the pre-refactor formula.
# ---------------------------------------------------------------------------


class TestConfidenceMatchesSharedEngine:
    """`round(max(0.0, base - penalty), 2)` was the exact pre-refactor
    formula; every case here reproduces it via
    `calculate_weighted_confidence` instead and asserts the same result."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "readiness_status,warning_count,expected",
        [
            ("ready", 0, 0.9),  # no warnings: override never triggers
            ("needs_revision", 0, 0.6),
            ("needs_revision", 2, 0.5),
            ("needs_revision", 10, 0.4),  # penalty capped at 0.2
            ("not_ready", 0, 0.3),
            ("not_ready", 1, 0.25),
        ],
    )
    async def test_confidence_value(self, readiness_status, warning_count, expected) -> None:
        planning_result = _planning_result()
        if warning_count:
            planning_result["verification_warnings"] = [
                f"unverified claim #{i}" for i in range(warning_count)
            ]
        workflow = _make_workflow(
            [
                _make_run("planning", "completed", planning_result),
                _make_run("development", "completed", _development_result()),
                _make_run("testing", "completed", _testing_result()),
                _make_run(
                    "documentation_planning", "completed", _documentation_planning_result()
                ),
            ]
        )
        context = _make_context(workflow=workflow)

        with patch(
            "app.agents.engineering_review.agent._call_llm",
            new=AsyncMock(return_value=_make_llm_response(readiness_status=readiness_status)),
        ):
            output = await EngineeringReviewAgent().run(context)

        assert output.confidence.score == expected

    @pytest.mark.asyncio
    async def test_confidence_reflects_the_downgraded_status_not_the_llms_original_ready(
        self,
    ) -> None:
        """Confidence is computed AFTER the deterministic 'ready' downgrade
        (Requirement 4) — an LLM verdict of 'ready' with 1 carried-forward
        warning must score as 'needs_revision' minus the warning penalty
        (0.6 - 0.05 = 0.55), never as 'ready' (0.9)."""
        planning_with_warning = _planning_result()
        planning_with_warning["verification_warnings"] = ["unverified claim"]
        workflow = _make_workflow(
            [
                _make_run("planning", "completed", planning_with_warning),
                _make_run("development", "completed", _development_result()),
                _make_run("testing", "completed", _testing_result()),
                _make_run(
                    "documentation_planning", "completed", _documentation_planning_result()
                ),
            ]
        )
        context = _make_context(workflow=workflow)

        with patch(
            "app.agents.engineering_review.agent._call_llm",
            new=AsyncMock(return_value=_make_llm_response(readiness_status="ready")),
        ):
            output = await EngineeringReviewAgent().run(context)

        assert output.result["readiness_status"] == "needs_revision"  # downgraded
        assert output.confidence.score == 0.55

    @pytest.mark.asyncio
    async def test_unrecognized_readiness_status_falls_back_to_0_5(self) -> None:
        """readiness_status has no enum constraint in the schema (a plain
        `str`) — an LLM response outside the documented three values must
        still resolve to the same 0.5 fallback the old `dict.get(status,
        0.5)` produced, not silently become 0.0."""
        context = _make_context(workflow=_full_workflow())

        with patch(
            "app.agents.engineering_review.agent._call_llm",
            new=AsyncMock(return_value=_make_llm_response(readiness_status="something_else")),
        ):
            output = await EngineeringReviewAgent().run(context)

        assert output.confidence.score == 0.5

    @pytest.mark.asyncio
    async def test_reasoning_comes_from_shared_engine(self) -> None:
        """The reasoning string format is now the shared engine's own
        auditable output, not this agent's bespoke sentence — an
        intentional, non-functional change (see task deliverable)."""
        context = _make_context(workflow=_full_workflow())

        with patch(
            "app.agents.engineering_review.agent._call_llm",
            new=AsyncMock(return_value=_make_llm_response(readiness_status="ready")),
        ):
            output = await EngineeringReviewAgent().run(context)

        assert "Deterministic confidence" in output.confidence.reasoning
        assert "ready=True" in output.confidence.reasoning

    def test_no_local_confidence_arithmetic_remains(self) -> None:
        """Regression guard for the Delta Report finding: the module must
        no longer compute a base-minus-penalty score by hand — only via
        the shared engine."""
        import inspect

        from app.agents.engineering_review import agent as eng_review_agent

        source = inspect.getsource(eng_review_agent)
        assert "calculate_weighted_confidence" in source
        # The old hand-rolled formula shape — must not reappear.
        assert "base_confidence - warning_penalty" not in source


# ---------------------------------------------------------------------------
# The actual regression this rewrite exists to cover: full structured
# artifacts reach the LLM prompt, not a 256-char freetext cut.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_engineering_review_agent_prompt_contains_full_development_artifacts() -> None:
    """Development's real field names (`repositories`/`components`, not
    `affected_repositories`/`affected_components`) must actually reach the
    LLM prompt — this is the exact field-name mismatch that used to make
    Development's picks silently vanish from Engineering Review's input."""
    context = _make_context(workflow=_full_workflow())
    captured_prompt: dict[str, str] = {}

    async def _capture(user_prompt: str, **_kwargs: object) -> str:
        captured_prompt["prompt"] = user_prompt
        return _make_llm_response()

    with patch(
        "app.agents.engineering_review.agent._call_llm",
        new=AsyncMock(side_effect=_capture),
    ):
        await EngineeringReviewAgent().run(context)

    prompt = captured_prompt["prompt"]
    assert "RateLimiterService" in prompt
    assert "payment-service" in prompt
    assert "acme" in prompt  # AffectedRepository.owner
    assert "src/main/RateLimiterService.java" in prompt  # AffectedComponent.file_path
    assert "Centralize config in a shared ConfigMap." in prompt  # Risk.mitigation
    assert "Regression tests cover" not in prompt  # sanity: not the LLM's own output
    # Testing's real fields also present, not dropped.
    assert "Requests over the limit are rejected with 429." in prompt
    # Documentation Planning's real fields also present, not dropped.
    assert "README.md" in prompt
    assert "New RateLimiterService is not mentioned." in prompt


@pytest.mark.asyncio
async def test_engineering_review_agent_reports_full_context_read_in_evidence() -> None:
    context = _make_context(workflow=_full_workflow())

    with patch(
        "app.agents.engineering_review.agent._call_llm",
        new=AsyncMock(return_value=_make_llm_response()),
    ):
        output = await EngineeringReviewAgent().run(context)

    tool_evidence = next(e for e in output.evidence if e.kind == "tool_call")
    assert "get_stage_result" in tool_evidence.summary
    assert "Missing" not in tool_evidence.summary


# ---------------------------------------------------------------------------
# Defensive fallback: no workflow, or a workflow missing prior stages.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_engineering_review_agent_missing_workflow_falls_back_gracefully() -> None:
    context = _make_context(workflow=None)

    with patch(
        "app.agents.engineering_review.agent._call_llm",
        new=AsyncMock(return_value=_make_llm_response()),
    ):
        output = await EngineeringReviewAgent().run(context)

    assert output.result["readiness_status"] == "ready"
    tool_evidence = next(e for e in output.evidence if e.kind == "tool_call")
    assert (
        "Missing: Planning, Development, Testing, Documentation Planning."
        in tool_evidence.summary
    )


@pytest.mark.asyncio
async def test_engineering_review_agent_partial_workflow_notes_missing_stages() -> None:
    """Only Planning completed — Development/Testing missing (e.g. a
    workflow reviewed out of order in a test, or a genuinely incomplete
    one). The prompt must say so explicitly, not silently omit them."""
    workflow = _make_workflow([_make_run("planning", "completed", _planning_result())])
    context = _make_context(workflow=workflow)
    captured_prompt: dict[str, str] = {}

    async def _capture(user_prompt: str, **_kwargs: object) -> str:
        captured_prompt["prompt"] = user_prompt
        return _make_llm_response()

    with patch(
        "app.agents.engineering_review.agent._call_llm",
        new=AsyncMock(side_effect=_capture),
    ):
        output = await EngineeringReviewAgent().run(context)

    assert "(No completed Development stage result available.)" in captured_prompt["prompt"]
    assert "(No completed Testing stage result available.)" in captured_prompt["prompt"]
    tool_evidence = next(e for e in output.evidence if e.kind == "tool_call")
    assert "Missing: Development, Testing, Documentation Planning." in tool_evidence.summary


# ---------------------------------------------------------------------------
# LLM failure, schema defaults, manifest, registration, selector
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_engineering_review_agent_llm_failure_raises() -> None:
    context = _make_context(workflow=_full_workflow())

    with patch(
        "app.agents.engineering_review.agent._call_llm",
        new=AsyncMock(side_effect=EngineeringReviewLLMError("Timeout")),
    ):
        agent = EngineeringReviewAgent()
        with pytest.raises(EngineeringReviewLLMError):
            await agent.run(context)


def test_engineering_readiness_report_schema_defaults() -> None:
    report = EngineeringReadinessReport(goal="x", executive_summary="y")
    assert report.completeness_findings == []
    assert report.blocking_issues == []
    data = report.model_dump()
    assert "graph_context_used" not in data
    assert "repositories_consulted" not in data


# ---------------------------------------------------------------------------
# Manifest and registration
# ---------------------------------------------------------------------------


def test_engineering_review_manifest_fields() -> None:
    assert ENGINEERING_REVIEW_MANIFEST.agent_id == "engineering_review"
    assert "review_readiness" in ENGINEERING_REVIEW_MANIFEST.goals
    assert "freetext" in ENGINEERING_REVIEW_MANIFEST.accepted_subject_types
    assert ENGINEERING_REVIEW_MANIFEST.output_schema_name == "EngineeringReadinessReport"


def test_engineering_review_agent_registered_in_global_registry() -> None:
    from app.agents.setup import register_agents
    from app.orchestrator.registry import global_registry

    register_agents()
    agent_ids = {m.agent_id for m in global_registry.all_manifests()}
    assert "engineering_review" in agent_ids


def test_selector_routes_review_readiness_goal() -> None:
    from app.agents.setup import register_agents
    from app.orchestrator.registry import global_registry
    from app.orchestrator.selector import AgentSelector

    register_agents()
    selector = AgentSelector(global_registry)
    assert selector.select("review_readiness") == "engineering_review"


# ---------------------------------------------------------------------------
# Weakness #5 — cross_repository_impact's dependency_type/confidence/evidence
# are graph-derived (backfilled from Context Discovery's canonical
# `repositories` list), never invented by the LLM.
# ---------------------------------------------------------------------------


def _context_discovery_result_with_repositories(repositories: list[dict]) -> dict:
    return {"repositories": repositories}


def test_cross_repository_impact_is_backfilled_from_graph_data() -> None:
    from app.agents.engineering_review.agent import _parse_llm_response

    context_discovery_result = _context_discovery_result_with_repositories(
        [
            {
                "name": "etl-core",
                "source": "suggested",
                "selected": True,
                "reason": "Shares Kafka topic 'orders-created' with ingestion-framework.",
                "relationship": "SHARES_TOPIC",
                "confidence": "structural",
            }
        ]
    )
    raw = json.dumps(
        {
            "executive_summary": "ok",
            "cross_repository_impact": [
                {
                    "repository": "etl-core",
                    "depends_on": ["ingestion-framework"],
                    "concern": "Blueprint doesn't mention the shared topic contract.",
                }
            ],
        }
    )

    report = _parse_llm_response(raw, "plan_freeform", context_discovery_result)

    assert len(report.cross_repository_impact) == 1
    impact = report.cross_repository_impact[0]
    # LLM-assessed field, passed through as-is.
    assert impact.concern == "Blueprint doesn't mention the shared topic contract."
    # Graph-derived fields — never present in the LLM's own JSON above.
    assert impact.dependency_type == "SHARES_TOPIC"
    assert impact.confidence == "structural"
    assert impact.evidence == ["Shares Kafka topic 'orders-created' with ingestion-framework."]


def test_cross_repository_impact_backfill_is_empty_for_an_unknown_repository() -> None:
    """The LLM is free to name a repository Context Discovery never
    suggested (e.g. inferred from prose in the blueprint) — the graph-derived
    fields must stay honestly empty, never fabricated to match."""
    from app.agents.engineering_review.agent import _parse_llm_response

    context_discovery_result = _context_discovery_result_with_repositories([])
    raw = json.dumps(
        {
            "executive_summary": "ok",
            "cross_repository_impact": [
                {"repository": "some-other-repo", "depends_on": [], "concern": "unclear"}
            ],
        }
    )

    report = _parse_llm_response(raw, "plan_freeform", context_discovery_result)

    impact = report.cross_repository_impact[0]
    assert impact.dependency_type == ""
    assert impact.confidence == ""
    assert impact.evidence == []


def test_cross_repository_impact_backfill_handles_missing_context_discovery_result() -> None:
    """No Context Discovery result at all (a result predating this field,
    or a run with none) must degrade to empty graph-derived fields, not
    raise."""
    from app.agents.engineering_review.agent import _parse_llm_response

    raw = json.dumps(
        {
            "executive_summary": "ok",
            "cross_repository_impact": [
                {"repository": "etl-core", "depends_on": [], "concern": "unclear"}
            ],
        }
    )

    report = _parse_llm_response(raw, "plan_freeform", None)

    impact = report.cross_repository_impact[0]
    assert impact.dependency_type == ""
    assert impact.confidence == ""


def test_format_repository_relationships_block_includes_relationship_and_confidence() -> None:
    from app.agents.stage_context import format_repository_relationships_block

    result = {
        "explicit_repositories": [{"name": "ingestion-framework"}],
        "suggested_repositories": [
            {
                "name": "etl-core",
                "reason": "Shares Kafka topic 'orders-created' with ingestion-framework.",
                "relationship": "SHARES_TOPIC",
                "confidence": "structural",
            }
        ],
        "selected_repositories": [{"name": "ingestion-framework"}, {"name": "etl-core"}],
    }

    block = format_repository_relationships_block(result)

    assert "SHARES_TOPIC" in block
    assert "structural" in block
    assert "etl-core" in block
