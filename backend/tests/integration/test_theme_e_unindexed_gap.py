"""ADR 0010 §7 P3 (Theme E) end-to-end: a request naming a tracked-but-
unindexed repository becomes a visible, actionable gap — real Postgres,
real reasoning engine, no mocked DB.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.context_pipeline.reasoning.engine import discover
from app.context_pipeline.reasoning.investigation import SessionContext
from app.context_pipeline.reasoning.investigators import (
    GraphInvestigator,
    RequestParseInvestigator,
)
from app.context_pipeline.reasoning.projection import build_result
from app.models.repository import Repository
from app.models.user import User
from app.tools.interfaces import ToolResult

pytestmark = pytest.mark.asyncio


async def _make_user(db: AsyncSession) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"{uuid.uuid4()}@example.com",
        full_name="Test User",
        auth_provider="local",
    )
    db.add(user)
    await db.flush()
    return user


async def _make_tracked_repository(db: AsyncSession, user: User, name: str) -> Repository:
    repo = Repository(
        id=uuid.uuid4(),
        user_id=user.id,
        github_repo_id=str(uuid.uuid4().int)[:10],
        owner="acme",
        name=name,
        full_name=f"acme/{name}",
        private=False,
        default_branch="main",
        html_url=f"https://github.com/acme/{name}",
    )
    db.add(repo)
    await db.flush()
    return repo


def _graph_tool_result(repositories: list[str]) -> ToolResult:
    """Only `payment-service` is *indexed* — `streaming-pipeline` is tracked
    (a real Postgres row) but never appears in `indexed_repositories`,
    exactly the tracked-but-unindexed gap this test exercises."""
    return ToolResult(
        tool_id="neo4j_graph",
        tool_name="Neo4j Graph",
        success=True,
        data={
            "indexed_repositories": [{"name": n} for n in repositories],
            "components": [],
            "kafka_topics": [],
            "context_text": "x",
            "_repos_succeeded": True,
            "_traverse_succeeded": True,
            "_repos_summary": "x",
            "_traverse_summary": "x",
        },
        summary="q",
    )


async def test_a_tracked_but_unindexed_repository_becomes_a_visible_gap(
    db_session: AsyncSession,
) -> None:
    user = await _make_user(db_session)
    await _make_tracked_repository(db_session, user, "payment-service")
    await _make_tracked_repository(db_session, user, "streaming-pipeline")
    await db_session.flush()

    with patch(
        "app.context_pipeline.reasoning.investigators.GraphProvider.retrieve",
        new=AsyncMock(return_value=_graph_tool_result(["payment-service"])),
    ):
        state = await discover(
            request="Also update streaming-pipeline once payment-service ships",
            session=SessionContext(db=db_session, user_id=user.id),
            investigators=[RequestParseInvestigator(), GraphInvestigator()],
        )

    unindexed_refs = [
        f
        for f in state.ledger.facts_of("reference")
        if f.subject == "streaming-pipeline" and f.value.get("indexed") is False
    ]
    assert unindexed_refs, "streaming-pipeline must be recorded as a tracked-but-unindexed match"

    result = build_result(state)
    repository_signal = next(
        s
        for s in result["discovery_report"]["confidence_breakdown"]
        if s["capability"] == "repository"
    )
    matched = next(
        s
        for s in repository_signal["signals"]
        if s["label"] == "Request names a repository that matched an indexed one"
    )
    assert "streaming-pipeline" in matched["detail"]
    assert "hasn't been indexed yet" in matched["detail"]

    # payment-service (indexed, and the only indexed repo) is still
    # correctly identified and selected — the unindexed mention is
    # additional information, not a replacement for what did resolve.
    selected_names = {r["name"] for r in result["selected_repositories"]}
    assert selected_names == {"payment-service"}
