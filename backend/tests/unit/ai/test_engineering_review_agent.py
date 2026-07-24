"""Unit tests for the Engineering Review Agent.

Unlike Planning/Development/Testing, this agent runs no graph tools —
only the LLM call is mocked. Covers: happy path evidence/result shape,
readiness-status-driven confidence, LLM failure, manifest, registration,
and selector routing.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from app.agents._contract import AgentContext, Subject
from app.agents.engineering_review.agent import (
    EngineeringReviewAgent,
    EngineeringReviewLLMError,
)
from app.agents.engineering_review.manifest import ENGINEERING_REVIEW_MANIFEST
from app.agents.engineering_review.schemas import EngineeringReadinessReport


def _make_context(
    display_name: str = "Add rate limiting\n--- Context from Planning ---",
) -> AgentContext:
    subject = Subject(
        subject_id="freetext:abc123",
        subject_type="freetext",
        display_name=display_name,
    )
    return AgentContext(subject=subject, goal="review_readiness", extras={"db": AsyncMock()})


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


@pytest.mark.asyncio
async def test_engineering_review_agent_happy_path() -> None:
    context = _make_context()

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

    assert output.agent_id == "engineering_review"
    assert output.subject_id == "freetext:abc123"
    assert output.prompt_version == "1.0"
    # No graph_context_used / repositories_consulted on this schema —
    # this agent never touches the graph.
    assert "graph_context_used" not in output.result


@pytest.mark.asyncio
async def test_engineering_review_agent_confidence_tracks_readiness_status() -> None:
    context = _make_context()

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
async def test_engineering_review_agent_llm_failure_raises() -> None:
    context = _make_context()

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
