"""Unit tests for DocumentationReviewAgent (goal=review_documentation).

Real DB (repository lookup, via the transactional `db_session` fixture),
mocked I/O boundaries: repository cloning, GitHub token lookup, the Neo4j
graph read, and the LLM synthesis call — the same boundary-mocking style
`test_context_providers.py` uses for ConfluenceProvider.
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
from app.agents.documentation.agent import (
    DocumentationReviewAgent,
    _strip_json_fence,
    resolve_repository_subject,
)
from app.core.exceptions import NotFoundError
from app.models.repository import Repository
from app.models.user import User


def test_strip_json_fence_removes_a_json_labeled_fence() -> None:
    text = '```json\n{"a": 1}\n```'
    assert _strip_json_fence(text) == '{"a": 1}'


def test_strip_json_fence_removes_a_bare_fence() -> None:
    text = '```\n{"a": 1}\n```'
    assert _strip_json_fence(text) == '{"a": 1}'


def test_strip_json_fence_is_a_no_op_for_bare_json() -> None:
    text = '{"a": 1}'
    assert _strip_json_fence(text) == '{"a": 1}'


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
        goal="review_documentation",
        extras={"db": db_session, "user_id": other_user_id},
    )

    with pytest.raises(NotFoundError):
        await DocumentationReviewAgent().run(context)


@pytest.mark.asyncio
async def test_run_produces_findings_and_evidence_end_to_end(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    repository = await _make_user_and_repository(db_session)
    (tmp_path / "README.md").write_text("See [missing](docs/missing.md).")

    llm_json = json.dumps(
        {
            "summary": "Documentation is thin.",
            "findings": [
                {
                    "finding_type": "missing",
                    "severity": "high",
                    "file_path": "(missing)",
                    "description": "No architecture documentation exists.",
                }
            ],
            "proposed_updates": [
                {
                    "file_path": "README.md",
                    "rationale": "Fix broken link.",
                    "proposed_markdown": "# Widgets\n\nNo broken links here.",
                }
            ],
            "proposed_new_documents": [
                {
                    "file_path": "docs/architecture.md",
                    "title": "Architecture",
                    "rationale": "Nothing documents the architecture.",
                    "proposed_markdown": "# Architecture\n",
                }
            ],
        }
    )

    context = AgentContext(
        subject=resolve_repository_subject(repository),
        goal="review_documentation",
        extras={"db": db_session, "user_id": repository.user_id},
    )

    with (
        patch(
            "app.agents.documentation.agent.clone_repository",
            new=_fake_clone(tmp_path),
        ),
        patch(
            "app.agents.documentation.agent.get_decrypted_access_token",
            new=AsyncMock(return_value="fake-token"),
        ),
        patch(
            "app.agents.documentation.agent.Neo4jGraphRepository",
        ) as mock_graph_repo_cls,
        patch("app.agents.documentation.agent.StageAwareLLMProvider") as mock_provider_cls,
    ):
        mock_graph_repo_cls.return_value.get_nodes_by_label = AsyncMock(return_value=[])
        mock_provider = mock_provider_cls.return_value
        mock_response = AsyncMock()
        mock_response.text = llm_json
        mock_provider.complete = AsyncMock(return_value=mock_response)

        output = await DocumentationReviewAgent().run(context)

    assert output.agent_id == "documentation_review"
    result = output.result
    assert result["repository_full_name"] == "acme/widgets"
    assert len(result["files_reviewed"]) == 1
    assert result["files_reviewed"][0]["path"] == "README.md"

    finding_types = {f["finding_type"] for f in result["findings"]}
    assert "broken_link" in finding_types  # deterministic check
    assert "missing" in finding_types  # LLM-reported

    assert len(result["proposed_updates"]) == 1
    assert result["proposed_updates"][0]["file_path"] == "README.md"
    assert len(result["proposed_new_documents"]) == 1
    assert result["proposed_new_documents"][0]["file_path"] == "docs/architecture.md"

    assert any(e.kind == "llm_reasoning" for e in output.evidence)
    assert any(e.kind == "graph_traversal" for e in output.evidence)
    assert output.confidence.score > 0


@pytest.mark.asyncio
async def test_run_drops_a_proposed_update_for_an_unknown_file(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """A defensive guard: the LLM must not be trusted to only ever name
    files that were actually discovered — an update pointing at a file
    this run never saw is silently dropped rather than passed through."""
    repository = await _make_user_and_repository(db_session)
    (tmp_path / "README.md").write_text("Fine.")

    llm_json = json.dumps(
        {
            "summary": "ok",
            "findings": [],
            "proposed_updates": [
                {
                    "file_path": "docs/never-discovered.md",
                    "rationale": "hallucinated",
                    "proposed_markdown": "nope",
                }
            ],
            "proposed_new_documents": [],
        }
    )

    context = AgentContext(
        subject=resolve_repository_subject(repository),
        goal="review_documentation",
        extras={"db": db_session, "user_id": repository.user_id},
    )

    with (
        patch("app.agents.documentation.agent.clone_repository", new=_fake_clone(tmp_path)),
        patch(
            "app.agents.documentation.agent.get_decrypted_access_token",
            new=AsyncMock(return_value=None),
        ),
        patch("app.agents.documentation.agent.Neo4jGraphRepository") as mock_graph_repo_cls,
        patch("app.agents.documentation.agent.StageAwareLLMProvider") as mock_provider_cls,
    ):
        mock_graph_repo_cls.return_value.get_nodes_by_label = AsyncMock(return_value=[])
        mock_response = AsyncMock()
        mock_response.text = llm_json
        mock_provider_cls.return_value.complete = AsyncMock(return_value=mock_response)

        output = await DocumentationReviewAgent().run(context)

    assert output.result["proposed_updates"] == []
