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
