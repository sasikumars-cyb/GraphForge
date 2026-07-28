"""Unit tests for the Documentation Planning Agent.

Like Engineering Review, this agent runs no graph tools — only the LLM
call is mocked. Covers: happy path evidence/result shape, impact-driven
confidence, LLM failure, manifest, registration, selector routing, and
that it reads full structured Planning/Development/Testing results via
get_stage_result() rather than a truncated summary.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.agents._contract import AgentContext, Subject
from app.agents.documentation_planning.agent import (
    DocumentationPlanningAgent,
    DocumentationPlanningLLMError,
)
from app.agents.documentation_planning.manifest import DOCUMENTATION_PLANNING_MANIFEST
from app.agents.documentation_planning.schemas import DocumentationPlan

# ---------------------------------------------------------------------------
# Helpers — SimpleNamespace-based workflow/run fakes, matching the pattern
# established in test_engineering_review_agent.py for agents that read
# prior stage results via get_stage_result().
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
    return AgentContext(subject=subject, goal="plan_documentation", extras=extras)


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
        "dependencies": [],
        "reusable_implementations": [],
        "implementation_phases": [],
        "risks": [],
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
    documentation_impact: str = "medium",
    required_updates: list[dict] | None = None,
) -> str:
    return json.dumps(
        {
            "executive_summary": "The rate limiter needs a README update and a new runbook.",
            "documentation_impact": documentation_impact,
            "impact_explanation": "New service plus a new API-facing behavior.",
            "required_updates": required_updates
            if required_updates is not None
            else [
                {
                    "document": "README.md",
                    "category": "repository",
                    "current_status": "Not confirmed — inferred from repository name only.",
                    "action": "update",
                    "reason": "New RateLimiterService is not mentioned anywhere.",
                    "priority": "medium",
                    "owner": "Backend maintainer",
                    "estimated_effort": "small",
                    "dependencies": [],
                },
            ],
            "new_documentation": [
                {
                    "name": "Rate Limiting Runbook",
                    "category": "operational",
                    "purpose": "How to tune limiter thresholds in production.",
                    "suggested_location": "docs/runbooks/rate-limiting.md",
                    "owner": "SRE",
                    "priority": "medium",
                    "estimated_effort": "medium",
                },
            ],
            "existing_updates": [
                {
                    "file_path": "README.md",
                    "sections_affected": ["Architecture"],
                    "summary_of_changes": "Mention RateLimiterService.",
                },
            ],
            "risks": [
                {
                    "description": "No runbook means on-call can't safely tune limits.",
                    "severity": "medium",
                },
            ],
            "recommendations": ["Write the runbook before rollout, not after."],
            "release_notes_draft": ["New feature: request rate limiting on the payment API."],
            "checklist": [
                {"label": "README updated", "applicable": True},
                {"label": "API documentation updated", "applicable": False},
                {"label": "Operational documentation updated", "applicable": True},
            ],
        }
    )


def _full_workflow() -> SimpleNamespace:
    return _make_workflow(
        [
            _make_run("planning", "completed", _planning_result()),
            _make_run("development", "completed", _development_result()),
            _make_run("testing", "completed", _testing_result()),
        ]
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_documentation_planning_agent_happy_path() -> None:
    context = _make_context(workflow=_full_workflow())

    with patch(
        "app.agents.documentation_planning.agent._call_llm",
        new=AsyncMock(return_value=_make_llm_response()),
    ):
        agent = DocumentationPlanningAgent()
        output = await agent.run(context)

    evidence_kinds = {e.kind for e in output.evidence}
    assert "tool_call" in evidence_kinds
    assert "llm_reasoning" in evidence_kinds

    assert output.result["executive_summary"]
    assert output.result["documentation_impact"] == "medium"
    assert len(output.result["required_updates"]) == 1
    assert len(output.result["new_documentation"]) == 1
    assert len(output.result["existing_updates"]) == 1
    assert len(output.result["risks"]) == 1
    assert len(output.result["checklist"]) == 3
    # Regression: `goal` used to be set to the entire blueprint context
    # string instead of the real goal, per the same bug class in Testing/
    # Engineering Review.
    assert output.result["goal"] == "plan_documentation"

    assert output.agent_id == "documentation_planning"
    assert output.subject_id == "freetext:abc123"
    assert output.prompt_version == "1.0"
    assert "graph_context_used" not in output.result


@pytest.mark.asyncio
async def test_documentation_planning_agent_confidence_tracks_impact() -> None:
    context = _make_context(workflow=_full_workflow())

    with patch(
        "app.agents.documentation_planning.agent._call_llm",
        new=AsyncMock(return_value=_make_llm_response(documentation_impact="high")),
    ):
        agent = DocumentationPlanningAgent()
        output = await agent.run(context)

    assert output.result["documentation_impact"] == "high"
    assert output.confidence.score >= 0.8


@pytest.mark.asyncio
async def test_documentation_planning_agent_none_impact_lower_confidence() -> None:
    context = _make_context(workflow=_full_workflow())

    with patch(
        "app.agents.documentation_planning.agent._call_llm",
        new=AsyncMock(
            return_value=_make_llm_response(documentation_impact="none", required_updates=[])
        ),
    ):
        agent = DocumentationPlanningAgent()
        output = await agent.run(context)

    assert output.result["documentation_impact"] == "none"
    assert output.confidence.score <= 0.65


# ---------------------------------------------------------------------------
# Full structured artifacts reach the LLM prompt, not a truncated summary.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_documentation_planning_agent_prompt_contains_full_prior_stage_artifacts() -> None:
    context = _make_context(workflow=_full_workflow())
    captured_prompt: dict[str, str] = {}

    async def _capture(user_prompt: str, **_kwargs: object) -> str:
        captured_prompt["prompt"] = user_prompt
        return _make_llm_response()

    with patch(
        "app.agents.documentation_planning.agent._call_llm",
        new=AsyncMock(side_effect=_capture),
    ):
        await DocumentationPlanningAgent().run(context)

    prompt = captured_prompt["prompt"]
    assert "RateLimiterService" in prompt
    assert "payment-service" in prompt
    assert "src/main/RateLimiterService.java" in prompt
    assert "Requests over the limit are rejected with 429." in prompt


@pytest.mark.asyncio
async def test_documentation_planning_agent_reports_full_context_read_in_evidence() -> None:
    context = _make_context(workflow=_full_workflow())

    with patch(
        "app.agents.documentation_planning.agent._call_llm",
        new=AsyncMock(return_value=_make_llm_response()),
    ):
        output = await DocumentationPlanningAgent().run(context)

    tool_evidence = next(e for e in output.evidence if e.kind == "tool_call")
    assert "get_stage_result" in tool_evidence.summary
    assert "Missing" not in tool_evidence.summary


# ---------------------------------------------------------------------------
# Defensive fallback: no workflow, or a workflow missing prior stages.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_documentation_planning_agent_missing_workflow_falls_back_gracefully() -> None:
    context = _make_context(workflow=None)

    with patch(
        "app.agents.documentation_planning.agent._call_llm",
        new=AsyncMock(return_value=_make_llm_response()),
    ):
        output = await DocumentationPlanningAgent().run(context)

    assert output.result["documentation_impact"] == "medium"
    tool_evidence = next(e for e in output.evidence if e.kind == "tool_call")
    assert "Missing: Planning, Development, Testing." in tool_evidence.summary


@pytest.mark.asyncio
async def test_documentation_planning_agent_partial_workflow_notes_missing_stages() -> None:
    workflow = _make_workflow([_make_run("planning", "completed", _planning_result())])
    context = _make_context(workflow=workflow)
    captured_prompt: dict[str, str] = {}

    async def _capture(user_prompt: str, **_kwargs: object) -> str:
        captured_prompt["prompt"] = user_prompt
        return _make_llm_response()

    with patch(
        "app.agents.documentation_planning.agent._call_llm",
        new=AsyncMock(side_effect=_capture),
    ):
        output = await DocumentationPlanningAgent().run(context)

    assert "(No completed Development stage result available.)" in captured_prompt["prompt"]
    assert "(No completed Testing stage result available.)" in captured_prompt["prompt"]
    tool_evidence = next(e for e in output.evidence if e.kind == "tool_call")
    assert "Missing: Development, Testing." in tool_evidence.summary


# ---------------------------------------------------------------------------
# Verification warnings carried forward from prior stages
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_documentation_planning_agent_carries_forward_prior_verification_warnings() -> None:
    planning_with_warning = _planning_result()
    planning_with_warning["verification_warnings"] = [
        "Repository 'billing-service' cited in this plan was not found among the "
        "repositories this run's graph traversal actually returned — unverified."
    ]
    workflow = _make_workflow(
        [
            _make_run("planning", "completed", planning_with_warning),
            _make_run("development", "completed", _development_result()),
            _make_run("testing", "completed", _testing_result()),
        ]
    )
    context = _make_context(workflow=workflow)

    with patch(
        "app.agents.documentation_planning.agent._call_llm",
        new=AsyncMock(return_value=_make_llm_response()),
    ):
        output = await DocumentationPlanningAgent().run(context)

    assert any(
        "billing-service" in w for w in output.result["prior_verification_warnings"]
    )


# ---------------------------------------------------------------------------
# LLM failure, schema defaults, manifest, registration, selector
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_documentation_planning_agent_llm_failure_raises() -> None:
    context = _make_context(workflow=_full_workflow())

    with patch(
        "app.agents.documentation_planning.agent._call_llm",
        new=AsyncMock(side_effect=DocumentationPlanningLLMError("Timeout")),
    ):
        agent = DocumentationPlanningAgent()
        with pytest.raises(DocumentationPlanningLLMError):
            await agent.run(context)


def test_documentation_plan_schema_defaults() -> None:
    plan = DocumentationPlan(goal="x", executive_summary="y")
    assert plan.required_updates == []
    assert plan.new_documentation == []
    assert plan.checklist == []
    data = plan.model_dump()
    assert "graph_context_used" not in data
    assert "repositories_consulted" not in data


# ---------------------------------------------------------------------------
# Manifest and registration
# ---------------------------------------------------------------------------


def test_documentation_planning_manifest_fields() -> None:
    assert DOCUMENTATION_PLANNING_MANIFEST.agent_id == "documentation_planning"
    assert "plan_documentation" in DOCUMENTATION_PLANNING_MANIFEST.goals
    assert "freetext" in DOCUMENTATION_PLANNING_MANIFEST.accepted_subject_types
    assert DOCUMENTATION_PLANNING_MANIFEST.output_schema_name == "DocumentationPlan"


def test_documentation_planning_agent_registered_in_global_registry() -> None:
    from app.agents.setup import register_agents
    from app.orchestrator.registry import global_registry

    register_agents()
    agent_ids = {m.agent_id for m in global_registry.all_manifests()}
    assert "documentation_planning" in agent_ids


def test_selector_routes_plan_documentation_goal() -> None:
    from app.agents.setup import register_agents
    from app.orchestrator.registry import global_registry
    from app.orchestrator.selector import AgentSelector

    register_agents()
    selector = AgentSelector(global_registry)
    assert selector.select("plan_documentation") == "documentation_planning"
