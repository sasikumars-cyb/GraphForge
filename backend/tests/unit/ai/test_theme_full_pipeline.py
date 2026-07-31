"""ADR 0010 §7 P4 — the full Context Discovery -> Planning pipeline test,
promised by the original review and required again by this ADR's own
roadmap. Exercises the real reasoning engine's `discover()`, the real
`build_result()` projection, and the real `PlanningAgent` reading that
result through the *workflow* path (`get_stage_result`), not the
standalone inline-discovery fallback — this is the path production
actually uses once Context Discovery is a first-class workflow stage.

Scenario: a Jira naming two repositories together (the exact motivating
case from the original bug report). Asserts `explicit_repositories` has
both, `selected_repositories` is auto-populated with both, readiness is not
BLOCKED, and Planning's own `target_repositories` contains both.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from app.agents._contract import AgentContext, Subject
from app.agents.planning.agent import PlanningAgent
from app.context_pipeline.reasoning.engine import discover
from app.context_pipeline.reasoning.investigation import SessionContext
from app.context_pipeline.reasoning.investigators import (
    GraphInvestigator,
    RequestParseInvestigator,
)
from app.context_pipeline.reasoning.projection import build_result
from app.models.agent_step import AgentStep
from app.models.run import Run
from app.models.workflow import Workflow
from app.tools.interfaces import ToolResult


def _graph_tool_result(repositories: list[str], components: list[tuple[str, str]]) -> ToolResult:
    return ToolResult(
        tool_id="neo4j_graph",
        tool_name="Neo4j Graph",
        success=True,
        data={
            "indexed_repositories": [{"name": n} for n in repositories],
            "components": [{"name": c, "repository": r, "type": "service"} for c, r in components],
            "kafka_topics": [],
            "context_text": "graph context",
            "_repos_succeeded": True,
            "_traverse_succeeded": True,
            "_repos_summary": f"{len(repositories)} repos",
            "_traverse_summary": f"{len(components)} components",
        },
        summary="queried",
    )


def _workflow_with_completed_context_discovery(result: dict) -> Workflow:
    workflow = Workflow(id=uuid.uuid4(), title="Test", current_stage="planning")
    run = Run(
        id=uuid.uuid4(),
        subject_id="freetext:test",
        subject_type="freetext",
        display_name="Test",
        goal="discover_context",
        status="completed",
        workflow_stage="context_discovery",
        created_at=datetime.now(UTC),
    )
    step = AgentStep(
        id=uuid.uuid4(),
        run_id=run.id,
        agent_id="context_discovery",
        status="completed",
        result=result,
    )
    run.steps = [step]
    workflow.runs = [run]
    return workflow


@pytest.mark.asyncio
async def test_full_pipeline_two_explicit_repositories_reach_planning() -> None:
    with patch(
        "app.context_pipeline.reasoning.investigators.GraphProvider.retrieve",
        new=AsyncMock(
            return_value=_graph_tool_result(
                ["ingestion-framework", "etl-core", "streaming-pipeline"],
                [
                    ("SchemaMerger", "ingestion-framework"),
                    ("DeltaWriter", "etl-core"),
                ],
            )
        ),
    ):
        state = await discover(
            request=(
                "Handle automatic schema evolution when source data introduces new nested "
                "struct fields. Enable Delta Lake mergeSchema. "
                "Repo: ingestion-framework, etl-core"
            ),
            session=SessionContext(db=None, user_id=None),  # type: ignore[arg-type]
            investigators=[RequestParseInvestigator(), GraphInvestigator()],
        )

    discovery_result = build_result(state)

    # --- Context Discovery's own output -----------------------------------
    explicit_names = {r["name"] for r in discovery_result["explicit_repositories"]}
    selected_names = {r["name"] for r in discovery_result["selected_repositories"]}
    assert explicit_names == {"ingestion-framework", "etl-core"}
    assert selected_names == {"ingestion-framework", "etl-core"}
    assert discovery_result["readiness"] != "BLOCKED"

    # --- Planning, reading it via the real workflow path --------------------
    workflow = _workflow_with_completed_context_discovery(discovery_result)
    context = AgentContext(
        subject=Subject(
            subject_id="freetext:abc123",
            subject_type="freetext",
            display_name="Handle automatic schema evolution",
        ),
        goal="plan_freeform",
        extras={"db": AsyncMock(), "user_id": "user-1", "workflow": workflow},
    )

    llm_response = json.dumps(
        {
            "executive_summary": "Enable mergeSchema across both repositories.",
            "implementation_steps": [
                {
                    "order": 1,
                    "description": "Enable mergeSchema",
                    "affected_component": "SchemaMerger",
                    "risk_note": "",
                }
            ],
            "affected_components": ["SchemaMerger"],
            "kafka_topics_involved": [],
            "risk_considerations": ["Schema drift"],
            "graph_context_used": True,
            "repository_usage": [
                {"name": "ingestion-framework", "purpose": "Schema detection"},
                {"name": "etl-core", "purpose": "Delta Lake writes"},
            ],
        }
    )

    with patch("app.agents.planning.agent._call_llm", new=AsyncMock(return_value=llm_response)):
        output = await PlanningAgent().run(context)

    assert set(output.result["target_repositories"]) == {"ingestion-framework", "etl-core"}
