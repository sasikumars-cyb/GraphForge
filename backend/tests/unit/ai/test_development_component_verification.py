"""ADR 0027 — Development Component Verification Enforcement.

End-to-end coverage of the wiring inside DevelopmentAgent.run(): that
`file_path_verification` is actually set on each persisted component, that
`component_repository_mismatch` is actually emitted (and is mutually
exclusive with the existing, unmodified `component_not_found`), and that
neither an LLM claim nor a Development-stage override can promote a
component's verification state (Invariants A/B, case 16/17).
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents._contract import AgentContext, Subject
from app.agents.development.agent import DevelopmentAgent
from app.graph.models import GraphNode, GraphPayload
from app.services.workflow_service import _OVERRIDABLE_FIELDS


def _context() -> AgentContext:
    subject = Subject(
        subject_id="freetext:dev-adr0027",
        subject_type="freetext",
        display_name="Add a JWT filter to OrderController",
    )
    return AgentContext(
        subject=subject,
        goal="develop_change_plan",
        extras={"db": AsyncMock(), "user_id": "user-1"},
    )


def _two_repo_rows():
    repo_a = MagicMock()
    repo_a.id = "repo-a-uuid"
    repo_a.name = "order-service"
    repo_a.owner = "acme"
    repo_a.full_name = "acme/order-service"

    repo_b = MagicMock()
    repo_b.id = "repo-b-uuid"
    repo_b.name = "payment-service"
    repo_b.owner = "acme"
    repo_b.full_name = "acme/payment-service"
    return repo_a, repo_b


def _mock_graph_repo_with_component_only_in_repo_a():
    """`OrderController` / `src/OrderController.java` exists only under
    order-service's graph data — payment-service has none."""
    mock_graph_repo = MagicMock()
    mock_graph_repo.has_graph = AsyncMock(return_value=True)

    component_node = GraphNode(
        id="c1",
        labels=["Component", "Controller"],
        properties={"name": "OrderController", "file_path": "src/OrderController.java"},
    )

    async def _get_nodes_by_label(repo_id, label):
        if label == "Component" and repo_id == "repo-a-uuid":
            return [component_node]
        return []

    mock_graph_repo.get_nodes_by_label = AsyncMock(side_effect=_get_nodes_by_label)
    mock_graph_repo.get_full_graph = AsyncMock(return_value=GraphPayload(nodes=[], edges=[]))
    return mock_graph_repo


def _llm_response(components: list[dict], extra_top_level: dict | None = None) -> str:
    payload = {
        "executive_summary": "Add JWT validation.",
        "repositories": [
            {"name": "order-service", "owner": "acme", "reason": "primary target"},
        ],
        "components": components,
        "dependencies": [],
        "reusable_implementations": [],
        "implementation_phases": [
            {
                "order": 1,
                "title": "Add filter",
                "description": "Add JWT filter to OrderController.",
                "affected_components": ["OrderController"],
                "estimated_complexity": "low",
                "depends_on_phases": [],
            }
        ],
        "risks": [],
        "recommendations": [],
    }
    if extra_top_level:
        payload.update(extra_top_level)
    return json.dumps(payload)


async def _run_with(mock_graph_repo, components: list[dict]):
    context = _context()
    mock_db = context.extras["db"]
    repo_a, repo_b = _two_repo_rows()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [repo_a, repo_b]
    mock_db.execute.return_value = mock_result

    with (
        patch("app.agents.development.agent.get_driver", return_value=MagicMock()),
        patch("app.agents.development.agent.Neo4jGraphRepository", return_value=mock_graph_repo),
        patch(
            "app.agents.development.agent._call_llm",
            new=AsyncMock(return_value=_llm_response(components)),
        ),
    ):
        agent = DevelopmentAgent()
        return await agent.run(context)


@pytest.mark.asyncio
async def test_case_2_wrong_repository_produces_mismatch_not_not_found() -> None:
    """Case 2, full outcome — a real file, claimed under the wrong
    repository, must be UNVERIFIED with a component_repository_mismatch
    finding, never VERIFIED and never (only) component_not_found."""
    mock_graph_repo = _mock_graph_repo_with_component_only_in_repo_a()
    output = await _run_with(
        mock_graph_repo,
        components=[
            {
                "name": "OrderController",
                "component_type": "Controller",
                "repository": "payment-service",  # wrong — real file is under order-service
                "file_path": "src/OrderController.java",
                "change_description": "Add JWT filter",
            }
        ],
    )

    component = output.result["components"][0]
    assert component["file_path_verification"] == "unverified"

    categories = {f["category"] for f in output.result["verification_findings"]}
    assert "component_repository_mismatch" in categories
    assert "component_not_found" not in categories  # mutually exclusive (§4.5)

    finding = next(
        f
        for f in output.result["verification_findings"]
        if f["category"] == "component_repository_mismatch"
    )
    assert finding["blocking"] is True


@pytest.mark.asyncio
async def test_case_21_correct_pair_verifies_with_no_mismatch_finding() -> None:
    """Case 21 — the positive inverse of case 2."""
    mock_graph_repo = _mock_graph_repo_with_component_only_in_repo_a()
    output = await _run_with(
        mock_graph_repo,
        components=[
            {
                "name": "OrderController",
                "component_type": "Controller",
                "repository": "order-service",  # correct
                "file_path": "src/OrderController.java",
                "change_description": "Add JWT filter",
            }
        ],
    )

    component = output.result["components"][0]
    assert component["file_path_verification"] == "verified"

    categories = {f["category"] for f in output.result["verification_findings"]}
    assert "component_repository_mismatch" not in categories
    assert "component_not_found" not in categories


@pytest.mark.asyncio
async def test_case_22_nowhere_found_is_component_not_found_not_mismatch() -> None:
    """Case 22 — a path that exists nowhere gets the existing,
    unmodified component_not_found category, never the new mismatch
    category."""
    mock_graph_repo = _mock_graph_repo_with_component_only_in_repo_a()
    output = await _run_with(
        mock_graph_repo,
        components=[
            {
                "name": "GhostComponent",
                "component_type": "Controller",
                "repository": "order-service",
                "file_path": "src/DoesNotExistAnywhere.java",
                "change_description": "Invented",
            }
        ],
    )

    component = output.result["components"][0]
    assert component["file_path_verification"] == "unverified"

    categories = {f["category"] for f in output.result["verification_findings"]}
    assert "component_not_found" in categories
    assert "component_repository_mismatch" not in categories


@pytest.mark.asyncio
async def test_case_16_llm_asserting_verified_is_ignored() -> None:
    """An LLM JSON payload that includes a 'verified'/
    'file_path_verification' key on a component must have zero effect —
    AffectedComponent construction uses an explicit keyword allowlist
    that never reads such a key (Invariant A)."""
    mock_graph_repo = _mock_graph_repo_with_component_only_in_repo_a()
    output = await _run_with(
        mock_graph_repo,
        components=[
            {
                "name": "OrderController",
                "component_type": "Controller",
                "repository": "payment-service",  # still wrong
                "file_path": "src/OrderController.java",
                "change_description": "Add JWT filter",
                "verified": True,  # an LLM attempting to self-assert verification
                "file_path_verification": "verified",  # and by the real field name too
            }
        ],
    )

    # The LLM's injected claims are structurally ignored — the real,
    # repository-scoped computation still correctly produces "unverified"
    # for this wrong-repository claim.
    component = output.result["components"][0]
    assert component["file_path_verification"] == "unverified"


def test_development_stage_has_no_overridable_fields_including_verification_state() -> None:
    """Invariant B — a human override can never inject, modify, or
    promote component verification state, because the 'development'
    stage has no overridable fields at all today. Regression-asserts
    this stays true rather than assuming it silently."""
    assert _OVERRIDABLE_FIELDS.get("development", frozenset()) == frozenset()
