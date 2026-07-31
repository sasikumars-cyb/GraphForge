"""Unit tests for GET /workflows/{workflow_id}/understanding.

Tests the endpoint's parsing boundary, DTO projection, debug toggle,
and error handling.  Uses dependency overrides and patches — no real DB
or auth.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.v1.dependencies import get_current_user
from app.core.exceptions import NotFoundError
from app.database.session import get_db_session
from app.main import create_app
from app.models.user import User

pytestmark = pytest.mark.asyncio

_WORKFLOW_ID = str(uuid.uuid4())
_URL = f"/api/v1/workflows/{_WORKFLOW_ID}/understanding"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_user() -> User:
    user = MagicMock(spec=User)
    user.id = uuid.uuid4()
    user.email = "test@example.com"
    user.role = "user"
    return user


def _cd_result(**overrides: Any) -> dict[str, Any]:
    """Minimal ContextDiscoveryResult-shaped dict."""
    base: dict[str, Any] = {
        "original_request": "Add Kafka consumer",
        "readiness": "READY",
        "blocking_reasons": [],
        "engineering_understanding": {
            "business_objective": "Implement Kafka consumer",
            "current_behavior": "No consumer exists",
            "desired_behavior": "Consume order events",
            "primary_repository": "org/order-service",
            "supporting_repositories": ["org/kafka-lib"],
            "implementation_ownership": ["order-team"],
            "architecture_relationships": ["order-service → kafka"],
            "constraints": ["Must use Avro schemas"],
            "remaining_unknowns": ["Schema registry URL"],
            "rejected_assumptions": [],
            "engineering_insights": ["Reuse existing deserializer"],
            "risks": ["Consumer lag under load"],
        },
        "evidence_package": {
            "items": [
                {
                    "name": "OrderConsumer",
                    "repository": "org/order-service",
                    "tier": "must_modify",
                    "relevance_score": 0.9,
                    "proximity_score": 0.8,
                    "repository_bonus": 0.0,
                    "test_penalty": 0.0,
                    "composite_score": 0.85,
                    "confidence": 0.9,
                    "reason": "Primary consumer class",
                },
            ],
            "excluded_count": 0,
        },
        "graph_topics": [{"name": "OrderEvents"}],
        "graph_components": [
            {"name": "OrderConsumer", "topic": "OrderEvents"},
        ],
        "discovery_report": {
            "confidence_breakdown": [
                {
                    "capability": "code_understanding",
                    "label": "Code understanding",
                    "necessity": "required",
                    "satisfied": True,
                    "score": 0.9,
                    "signals": [],
                },
                {
                    "capability": "documentation",
                    "label": "Documentation",
                    "necessity": "recommended",
                    "satisfied": True,
                    "score": 0.8,
                    "signals": [],
                },
            ],
            "gaps": [],
            "investigation": [{"evidence_id": "ev1", "action": "search"}],
            "findings": [{"kind": "component", "items": []}],
            "transcript": [{"step": "initial analysis"}],
        },
        "ranked_repository_names": ["org/order-service"],
        "capability_confidence": {"code_understanding": 0.9},
        "planning_metadata": {"cycles": 1},
        "working_memory": {},
        "assumptions": ["Schema registry is available"],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def app_client() -> AsyncGenerator[AsyncClient, None]:
    """Test client with auth and DB overridden."""
    app = create_app()
    fake_user = _fake_user()

    async def _override_user() -> User:
        return fake_user

    async def _override_db() -> AsyncGenerator:
        yield AsyncMock()

    app.dependency_overrides[get_current_user] = _override_user
    app.dependency_overrides[get_db_session] = _override_db

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test",
    ) as ac:
        yield ac
    app.dependency_overrides.clear()


def _mock_workflow() -> MagicMock:
    """A fake Workflow with preloaded runs."""
    wf = MagicMock()
    wf.id = uuid.UUID(_WORKFLOW_ID)
    wf.runs = []
    return wf


# ---------------------------------------------------------------------------
# Tests — Happy path
# ---------------------------------------------------------------------------


class TestHappyPath:
    """200 with correct DTO when context discovery is completed."""

    async def test_returns_200_with_dto(
        self, app_client: AsyncClient,
    ) -> None:
        with (
            patch(
                "app.services.workflow_service.get_workflow",
                new_callable=AsyncMock,
                return_value=_mock_workflow(),
            ),
            patch(
                "app.api.v1.routers.workflows.get_stage_result",
                return_value=_cd_result(),
            ),
        ):
            resp = await app_client.get(_URL)

        assert resp.status_code == 200
        body = resp.json()
        assert body["business_goal"] == "Implement Kafka consumer"
        assert body["current_situation"] == "No consumer exists"
        assert body["expected_outcome"] == "Consume order events"

    async def test_repository_summary(
        self, app_client: AsyncClient,
    ) -> None:
        with (
            patch(
                "app.services.workflow_service.get_workflow",
                new_callable=AsyncMock,
                return_value=_mock_workflow(),
            ),
            patch(
                "app.api.v1.routers.workflows.get_stage_result",
                return_value=_cd_result(),
            ),
        ):
            resp = await app_client.get(_URL)

        body = resp.json()
        assert body["repository_summary"]["primary"] == "org/order-service"
        assert body["repository_summary"]["supporting"] == ["org/kafka-lib"]

    async def test_relevant_areas_grouped(
        self, app_client: AsyncClient,
    ) -> None:
        with (
            patch(
                "app.services.workflow_service.get_workflow",
                new_callable=AsyncMock,
                return_value=_mock_workflow(),
            ),
            patch(
                "app.api.v1.routers.workflows.get_stage_result",
                return_value=_cd_result(),
            ),
        ):
            resp = await app_client.get(_URL)

        body = resp.json()
        areas = body["relevant_areas"]
        assert any(a["name"] == "OrderEvents" for a in areas)

    async def test_evidence_summary_populated(
        self, app_client: AsyncClient,
    ) -> None:
        with (
            patch(
                "app.services.workflow_service.get_workflow",
                new_callable=AsyncMock,
                return_value=_mock_workflow(),
            ),
            patch(
                "app.api.v1.routers.workflows.get_stage_result",
                return_value=_cd_result(),
            ),
        ):
            resp = await app_client.get(_URL)

        body = resp.json()
        assert len(body["evidence_summary"]) >= 1

    async def test_planning_assessment_ready(
        self, app_client: AsyncClient,
    ) -> None:
        with (
            patch(
                "app.services.workflow_service.get_workflow",
                new_callable=AsyncMock,
                return_value=_mock_workflow(),
            ),
            patch(
                "app.api.v1.routers.workflows.get_stage_result",
                return_value=_cd_result(),
            ),
        ):
            resp = await app_client.get(_URL)

        body = resp.json()
        assert body["planning_assessment"]["status"] == "READY"


# ---------------------------------------------------------------------------
# Tests — Not found
# ---------------------------------------------------------------------------


class TestNotFound:
    """404 when workflow or context discovery not found."""

    async def test_invalid_workflow_id(
        self, app_client: AsyncClient,
    ) -> None:
        resp = await app_client.get(
            "/api/v1/workflows/not-a-uuid/understanding",
        )
        assert resp.status_code == 404

    async def test_workflow_not_found(
        self, app_client: AsyncClient,
    ) -> None:
        with patch(
            "app.services.workflow_service.get_workflow",
            new_callable=AsyncMock,
            side_effect=NotFoundError("Workflow not found"),
        ):
            resp = await app_client.get(_URL)

        assert resp.status_code == 404

    async def test_no_context_discovery(
        self, app_client: AsyncClient,
    ) -> None:
        with (
            patch(
                "app.services.workflow_service.get_workflow",
                new_callable=AsyncMock,
                return_value=_mock_workflow(),
            ),
            patch(
                "app.api.v1.routers.workflows.get_stage_result",
                return_value=None,
            ),
        ):
            resp = await app_client.get(_URL)

        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Tests — Debug toggle
# ---------------------------------------------------------------------------


class TestDebugToggle:
    """debug=false → None; debug=true → populated bundle."""

    async def test_debug_false_returns_no_bundle(
        self, app_client: AsyncClient,
    ) -> None:
        with (
            patch(
                "app.services.workflow_service.get_workflow",
                new_callable=AsyncMock,
                return_value=_mock_workflow(),
            ),
            patch(
                "app.api.v1.routers.workflows.get_stage_result",
                return_value=_cd_result(),
            ),
        ):
            resp = await app_client.get(_URL)

        assert resp.json()["debug_bundle"] is None

    async def test_debug_true_returns_bundle(
        self, app_client: AsyncClient,
    ) -> None:
        with (
            patch(
                "app.services.workflow_service.get_workflow",
                new_callable=AsyncMock,
                return_value=_mock_workflow(),
            ),
            patch(
                "app.api.v1.routers.workflows.get_stage_result",
                return_value=_cd_result(),
            ),
        ):
            resp = await app_client.get(f"{_URL}?debug=true")

        body = resp.json()
        assert body["debug_bundle"] is not None
        assert body["debug_bundle"]["investigation_trail"] == [
            {"evidence_id": "ev1", "action": "search"},
        ]
        assert body["debug_bundle"]["repository_ranking"] == [
            "org/order-service",
        ]
        assert body["debug_bundle"]["assumptions"] == [
            "Schema registry is available",
        ]


# ---------------------------------------------------------------------------
# Tests — Documentation status derivation
# ---------------------------------------------------------------------------


class TestDocumentationStatusDerivation:
    """Endpoint derives documentation_status from capability + gaps."""

    async def test_satisfied(self, app_client: AsyncClient) -> None:
        with (
            patch(
                "app.services.workflow_service.get_workflow",
                new_callable=AsyncMock,
                return_value=_mock_workflow(),
            ),
            patch(
                "app.api.v1.routers.workflows.get_stage_result",
                return_value=_cd_result(),
            ),
        ):
            resp = await app_client.get(_URL)

        assert "satisfied" in resp.json()["documentation_status"].lower()

    async def test_unsatisfied_with_doc_gap(
        self, app_client: AsyncClient,
    ) -> None:
        result = _cd_result()
        result["discovery_report"]["confidence_breakdown"][1][
            "satisfied"
        ] = False
        result["discovery_report"]["gaps"] = [
            {
                "capability": "documentation",
                "summary": "API docs are outdated",
                "status": "open",
            },
        ]
        with (
            patch(
                "app.services.workflow_service.get_workflow",
                new_callable=AsyncMock,
                return_value=_mock_workflow(),
            ),
            patch(
                "app.api.v1.routers.workflows.get_stage_result",
                return_value=result,
            ),
        ):
            resp = await app_client.get(_URL)

        assert (
            resp.json()["documentation_status"] == "API docs are outdated"
        )

    async def test_no_doc_capability(
        self, app_client: AsyncClient,
    ) -> None:
        result = _cd_result()
        result["discovery_report"]["confidence_breakdown"] = [
            e
            for e in result["discovery_report"]["confidence_breakdown"]
            if e["capability"] != "documentation"
        ]
        with (
            patch(
                "app.services.workflow_service.get_workflow",
                new_callable=AsyncMock,
                return_value=_mock_workflow(),
            ),
            patch(
                "app.api.v1.routers.workflows.get_stage_result",
                return_value=result,
            ),
        ):
            resp = await app_client.get(_URL)

        assert "unknown" in resp.json()["documentation_status"].lower()


# ---------------------------------------------------------------------------
# Tests — Next step derivation
# ---------------------------------------------------------------------------


class TestNextStepDerivation:
    """Endpoint derives next_step from readiness + blocking reasons."""

    async def test_ready(self, app_client: AsyncClient) -> None:
        with (
            patch(
                "app.services.workflow_service.get_workflow",
                new_callable=AsyncMock,
                return_value=_mock_workflow(),
            ),
            patch(
                "app.api.v1.routers.workflows.get_stage_result",
                return_value=_cd_result(readiness="READY"),
            ),
        ):
            resp = await app_client.get(_URL)

        assert "ready" in resp.json()["next_step"].lower()

    async def test_blocked_with_reasons(
        self, app_client: AsyncClient,
    ) -> None:
        with (
            patch(
                "app.services.workflow_service.get_workflow",
                new_callable=AsyncMock,
                return_value=_mock_workflow(),
            ),
            patch(
                "app.api.v1.routers.workflows.get_stage_result",
                return_value=_cd_result(
                    readiness="BLOCKED",
                    blocking_reasons=["Missing graph"],
                ),
            ),
        ):
            resp = await app_client.get(_URL)

        assert "Missing graph" in resp.json()["next_step"]

    async def test_blocked_without_reasons(
        self, app_client: AsyncClient,
    ) -> None:
        with (
            patch(
                "app.services.workflow_service.get_workflow",
                new_callable=AsyncMock,
                return_value=_mock_workflow(),
            ),
            patch(
                "app.api.v1.routers.workflows.get_stage_result",
                return_value=_cd_result(readiness="BLOCKED"),
            ),
        ):
            resp = await app_client.get(_URL)

        assert "continue" in resp.json()["next_step"].lower()


# ---------------------------------------------------------------------------
# Tests — Parsing boundary
# ---------------------------------------------------------------------------


class TestParsingBoundary:
    """Endpoint correctly parses raw dicts into typed models."""

    async def test_invalid_readiness_defaults_to_blocked(
        self, app_client: AsyncClient,
    ) -> None:
        with (
            patch(
                "app.services.workflow_service.get_workflow",
                new_callable=AsyncMock,
                return_value=_mock_workflow(),
            ),
            patch(
                "app.api.v1.routers.workflows.get_stage_result",
                return_value=_cd_result(readiness="INVALID"),
            ),
        ):
            resp = await app_client.get(_URL)

        assert resp.json()["planning_assessment"]["status"] == "BLOCKED"

    async def test_not_applicable_capabilities_filtered(
        self, app_client: AsyncClient,
    ) -> None:
        result = _cd_result()
        result["discovery_report"]["confidence_breakdown"].append(
            {
                "capability": "deployment_topology",
                "label": "Deployment topology",
                "necessity": "not_applicable",
                "satisfied": False,
                "score": 0.0,
                "signals": [],
            },
        )
        with (
            patch(
                "app.services.workflow_service.get_workflow",
                new_callable=AsyncMock,
                return_value=_mock_workflow(),
            ),
            patch(
                "app.api.v1.routers.workflows.get_stage_result",
                return_value=result,
            ),
        ):
            resp = await app_client.get(_URL)

        reasons = resp.json()["planning_assessment"]["reasons"]
        descriptions = [r["description"] for r in reasons]
        assert not any("Deployment topology" in d for d in descriptions)

    async def test_empty_result_fields_handled(
        self, app_client: AsyncClient,
    ) -> None:
        """Minimal result with missing optional fields → no crash."""
        result: dict[str, Any] = {
            "engineering_understanding": {},
            "evidence_package": {},
            "readiness": "BLOCKED",
        }
        with (
            patch(
                "app.services.workflow_service.get_workflow",
                new_callable=AsyncMock,
                return_value=_mock_workflow(),
            ),
            patch(
                "app.api.v1.routers.workflows.get_stage_result",
                return_value=result,
            ),
        ):
            resp = await app_client.get(_URL)

        assert resp.status_code == 200
        body = resp.json()
        assert body["business_goal"] == ""
        assert body["planning_assessment"]["status"] == "BLOCKED"
        assert body["debug_bundle"] is None
