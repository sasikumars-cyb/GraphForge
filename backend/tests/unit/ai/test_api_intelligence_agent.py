"""Unit tests for ApiIntelligenceAgent (goal=analyze_api_intelligence).

Real DB (repository lookup, via the transactional `db_session` fixture),
mocked I/O boundaries: repository cloning, GitHub token lookup, and the LLM
synthesis call — the same boundary-mocking style `test_documentation_agent.py`
uses.
"""

from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents._contract import AgentContext
from app.agents.api_intelligence.agent import (
    ApiIntelligenceAgent,
    resolve_repository_subject,
)
from app.core.exceptions import NotFoundError
from app.models.repository import Repository
from app.models.user import User


async def _make_user_and_repository(db: AsyncSession) -> Repository:
    user = User(
        id=uuid.uuid4(),
        email=f"{uuid.uuid4().hex}@example.com",
        hashed_password="x",
        full_name="Test User",
    )
    db.add(user)
    await db.flush()

    repository = Repository(
        user_id=user.id,
        github_repo_id=str(uuid.uuid4().int)[:10],
        owner="acme",
        name="widgets",
        full_name="acme/widgets",
        private=False,
        default_branch="main",
        html_url="https://github.com/acme/widgets",
    )
    db.add(repository)
    await db.flush()
    return repository


def _fake_clone(tmp_path: Path):
    @asynccontextmanager
    async def _clone(html_url: str, ref: str, access_token: str | None = None):
        yield tmp_path

    return _clone


def test_resolve_repository_subject_shape() -> None:
    repository = Repository(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        github_repo_id="123",
        owner="acme",
        name="widgets",
        full_name="acme/widgets",
        private=False,
        default_branch="main",
        html_url="https://github.com/acme/widgets",
    )
    subject = resolve_repository_subject(repository)
    assert subject.subject_id == f"repo:{repository.id}"
    assert subject.subject_type == "repository"
    assert subject.display_name == "acme/widgets"


@pytest.mark.asyncio
async def test_run_rejects_a_repository_not_owned_by_this_user(db_session: AsyncSession) -> None:
    repository = await _make_user_and_repository(db_session)
    other_user_id = uuid.uuid4()

    context = AgentContext(
        subject=resolve_repository_subject(repository),
        goal="analyze_api_intelligence",
        extras={"db": db_session, "user_id": other_user_id},
    )

    with pytest.raises(NotFoundError):
        await ApiIntelligenceAgent().run(context)


@pytest.mark.asyncio
async def test_run_produces_extracted_api_surface_and_scores(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    repository = await _make_user_and_repository(db_session)
    (tmp_path / "api.md").write_text("# Widgets API\n\nGET /v1/widgets/{id}\n")
    (tmp_path / "README.md").write_text("See [api docs](api.md).")

    llm_json = json.dumps(
        {
            "executive_summary": "A widgets API with basic CRUD.",
            "base_urls": ["https://api.acme.com"],
            "endpoints": [
                {
                    "method": "GET",
                    "path": "/v1/widgets/{id}",
                    "base_url": "https://api.acme.com",
                    "description": "Fetch a widget",
                    "parameters": [
                        {
                            "name": "id",
                            "location": "path",
                            "type": "string",
                            "required": True,
                            "description": "Widget id",
                        }
                    ],
                    "request_example": "",
                    "response_example": '{"id": "1"}',
                    "status_codes": ["200", "404"],
                    "authentication_required": True,
                    "owner": "platform-team",
                    "version": "v1",
                    "source_file": "api.md",
                }
            ],
            "authentication": "Bearer tokens.",
            "authorization": "",
            "rate_limits": "",
            "pagination": "",
            "versioning": "",
            "dependencies": [],
            "assumptions": [],
            "todos": [],
            "open_questions": [],
            "security_findings": [
                {
                    "category": "rate_limiting",
                    "severity": "high",
                    "title": "No rate limiting documented",
                    "description": "No rate limit is mentioned anywhere.",
                    "why_it_matters": "Could allow abuse.",
                    "recommendation": "Document and enforce a rate limit.",
                    "confidence": 0.7,
                }
            ],
            "scores": {
                "documentation_completeness": 60,
                "security_score": 40,
                "api_quality_score": 70,
                "readability_score": 80,
                "consistency_score": 65,
                "overall_readiness_score": 58,
            },
            "missing_information": ["No documented error response schema."],
        }
    )

    context = AgentContext(
        subject=resolve_repository_subject(repository),
        goal="analyze_api_intelligence",
        extras={"db": db_session, "user_id": repository.user_id},
    )

    with (
        patch("app.agents.api_intelligence.agent.clone_repository", new=_fake_clone(tmp_path)),
        patch(
            "app.agents.api_intelligence.agent.get_decrypted_access_token",
            new=AsyncMock(return_value="fake-token"),
        ),
        patch("app.agents.api_intelligence.agent.StageAwareLLMProvider") as mock_provider_cls,
    ):
        mock_provider = mock_provider_cls.return_value
        mock_response = AsyncMock()
        mock_response.text = llm_json
        mock_provider.complete = AsyncMock(return_value=mock_response)

        output = await ApiIntelligenceAgent().run(context)

    assert output.agent_id == "api_intelligence"
    result = output.result
    assert result["repository_full_name"] == "acme/widgets"
    assert len(result["files_reviewed"]) == 2
    assert len(result["endpoints"]) == 1
    assert result["endpoints"][0]["method"] == "GET"
    assert result["scores"]["overall_readiness_score"] == 58
    assert len(result["security_findings"]) == 1
    assert result["missing_information"] == ["No documented error response schema."]

    # Deterministic relationship discovery: README.md really links to api.md.
    assert {
        "from_file": "README.md",
        "to_file": "api.md",
        "relationship_type": "links_to",
    } in result["document_relationships"]

    assert any(e.kind == "llm_reasoning" for e in output.evidence)
    assert any(e.reference == "discovery:discover_relationships" for e in output.evidence)
    assert output.confidence.score > 0


@pytest.mark.asyncio
async def test_run_never_touches_the_graph(db_session: AsyncSession, tmp_path: Path) -> None:
    """Phase 1 scope: this agent must never import/construct a graph
    repository — grepping the module for Neo4j symbols would also catch
    this, but asserting it behaviorally (no graph-related evidence kind
    ever appears) is the stronger guarantee."""
    repository = await _make_user_and_repository(db_session)
    (tmp_path / "api.md").write_text("# API\n")

    context = AgentContext(
        subject=resolve_repository_subject(repository),
        goal="analyze_api_intelligence",
        extras={"db": db_session, "user_id": repository.user_id},
    )

    with (
        patch("app.agents.api_intelligence.agent.clone_repository", new=_fake_clone(tmp_path)),
        patch(
            "app.agents.api_intelligence.agent.get_decrypted_access_token",
            new=AsyncMock(return_value=None),
        ),
        patch("app.agents.api_intelligence.agent.StageAwareLLMProvider") as mock_provider_cls,
    ):
        mock_provider = mock_provider_cls.return_value
        mock_response = AsyncMock()
        mock_response.text = json.dumps({"executive_summary": "x"})
        mock_provider.complete = AsyncMock(return_value=mock_response)

        output = await ApiIntelligenceAgent().run(context)

    assert not any(e.kind == "graph_traversal" for e in output.evidence)


@pytest.mark.asyncio
async def test_run_reports_missing_information_when_no_markdown_found(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    repository = await _make_user_and_repository(db_session)

    context = AgentContext(
        subject=resolve_repository_subject(repository),
        goal="analyze_api_intelligence",
        extras={"db": db_session, "user_id": repository.user_id},
    )

    with (
        patch("app.agents.api_intelligence.agent.clone_repository", new=_fake_clone(tmp_path)),
        patch(
            "app.agents.api_intelligence.agent.get_decrypted_access_token",
            new=AsyncMock(return_value=None),
        ),
    ):
        output = await ApiIntelligenceAgent().run(context)

    assert output.confidence.score == 0.0
    assert output.result["missing_information"]
