"""`InvestigationAgent` end-to-end: real Postgres rows, a real Neo4j graph
(built by the real indexing pipeline against the real `spring_boot_git_repo`
fixture), a stub `IVersionControlProvider`, and a stub `ILLMProvider` - the
same pattern as `test_impact_analysis_engine.py`, extended to also assert on
*which* tools the agent decided to call and the shape of its reasoning log.
"""

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.agent.investigation_agent import InvestigationAgent
from app.ai.schemas.analysis_result import AIAnalysisResult, ConfidenceScore
from app.analysis.graph.neo4j_impact_reader import Neo4jImpactGraphReader
from app.graph.models import GraphPayload
from app.graph.neo4j_repository import Neo4jGraphRepository
from app.graph.session import get_driver
from app.indexer.services.indexing_service import index_repository
from app.integrations.interfaces import ChangedFile, IVersionControlProvider
from app.models.pull_request import PullRequest
from app.models.repository import Repository
from app.models.user import User

pytestmark = pytest.mark.asyncio


class StubVersionControlProvider(IVersionControlProvider):
    """Unlike `test_impact_analysis_engine.py`'s stub, `get_diff` and
    `get_recent_file_authors` return real values here - the agent may
    decide to call either, and this test asserts on what it decided."""

    def __init__(self, changed_files: list[ChangedFile]) -> None:
        self._changed_files = changed_files

    async def get_diff(
        self, owner: str, repo: str, pull_number: int, access_token: str | None = None
    ) -> str:
        return "--- a/file\n+++ b/file\n@@ -1 +1 @@\n-old\n+new\n"

    async def get_recent_file_authors(
        self, owner: str, repo: str, file_paths: set[str], access_token: str | None = None
    ) -> dict[str, list[str]]:
        return {path: ["alice"] for path in file_paths}

    async def list_changed_files(
        self, owner: str, repo: str, pull_number: int, access_token: str | None = None
    ) -> list[ChangedFile]:
        return self._changed_files


def _fake_ai_result() -> AIAnalysisResult:
    return AIAnalysisResult(
        executive_summary="Stub summary.",
        confidence=ConfidenceScore(score=0.9, reasoning="Stub"),
        prompt_version="1.3",
    )


async def _create_repository_and_pull_request(
    db_session: AsyncSession, git_repo_path: Path
) -> tuple[Repository, PullRequest]:
    user = User(email=f"agent-{uuid.uuid4().hex[:8]}@example.com", full_name="Agent Test")
    db_session.add(user)
    await db_session.flush()

    repository = Repository(
        user_id=user.id,
        github_repo_id="1",
        owner="local",
        name="agent-test-repo",
        full_name="local/agent-test-repo",
        private=False,
        default_branch="main",
        html_url=str(git_repo_path),
    )
    db_session.add(repository)
    await db_session.flush()

    pull_request = PullRequest(
        repository_id=repository.id,
        github_pr_id="1",
        number=1,
        title="Test PR",
        state="open",
        is_draft=False,
        author_login="tester",
        html_url="https://example.invalid/pr/1",
        head_ref="feature",
        head_sha="abc123",
        base_ref="main",
        github_created_at=datetime.now(UTC),
        github_updated_at=datetime.now(UTC),
    )
    db_session.add(pull_request)
    await db_session.flush()

    return repository, pull_request


@pytest.fixture
async def indexed_repository_and_pr(
    db_session: AsyncSession, spring_boot_git_repo: Path
) -> AsyncGenerator[tuple[Repository, PullRequest], None]:
    repository, pull_request = await _create_repository_and_pull_request(
        db_session, spring_boot_git_repo
    )
    await index_repository(
        repository_id=str(repository.id), html_url=str(spring_boot_git_repo), ref="main"
    )
    yield repository, pull_request
    await Neo4jGraphRepository(get_driver()).replace_repository_graph(
        str(repository.id), GraphPayload()
    )


def _agent(
    db_session: AsyncSession, changed_files: list[ChangedFile], llm_result: AIAnalysisResult
) -> tuple[InvestigationAgent, AsyncMock]:
    driver = get_driver()
    llm_provider = AsyncMock()
    llm_provider.analyze = AsyncMock(return_value=llm_result)
    agent = InvestigationAgent(
        db=db_session,
        graph_repository=Neo4jGraphRepository(driver),
        impact_graph_reader=Neo4jImpactGraphReader(driver),
        version_control_provider=StubVersionControlProvider(changed_files),
        llm_provider=llm_provider,
    )
    return agent, llm_provider


async def test_kafka_producer_change_traverses_reads_diff_and_history(
    db_session: AsyncSession, indexed_repository_and_pr: tuple[Repository, PullRequest]
) -> None:
    """A Kafka producer change has direct nodes (traversal runs), ends up
    HIGH risk (diff read), and has service impact (git history read)."""
    _, pull_request = indexed_repository_and_pr
    changed = [
        ChangedFile(
            path="src/main/java/com/example/orders/OrderEventProducer.java", status="modified"
        )
    ]
    agent, llm_provider = _agent(db_session, changed, _fake_ai_result())

    investigation = await agent.investigate(pull_request.id)

    tools_called = {
        step.tool_selected for step in investigation.reasoning_log if step.tool_selected
    }
    assert "read_dependency_graph" in tools_called
    assert "traverse_dependency_graph" in tools_called
    assert "read_git_diff" in tools_called
    assert "read_git_history" in tools_called
    # No cross-repository impact in this single-repository fixture.
    assert "retrieve_repository_metadata" not in tools_called
    assert "read_indexing_information" not in tools_called

    llm_provider.analyze.assert_awaited_once()
    context = llm_provider.analyze.call_args.args[0]
    assert context.risk == "HIGH"
    assert "old" in context.diff_content or "new" in context.diff_content
    assert context.recent_file_authors

    assert investigation.analysis.executive_summary == "Stub summary."


async def test_dto_only_change_skips_traversal_and_checks_indexing(
    db_session: AsyncSession, indexed_repository_and_pr: tuple[Repository, PullRequest]
) -> None:
    """A DTO-only change matches no graph node - traversal is skipped, the
    agent instead checks indexing information, and neither the diff nor
    git history are worth fetching for a LOW-risk, no-impact change."""
    _, pull_request = indexed_repository_and_pr
    changed = [
        ChangedFile(path="src/main/java/com/example/orders/OrderDto.java", status="modified")
    ]
    agent, llm_provider = _agent(db_session, changed, _fake_ai_result())

    investigation = await agent.investigate(pull_request.id)

    tools_called = {
        step.tool_selected for step in investigation.reasoning_log if step.tool_selected
    }
    assert tools_called == {"read_dependency_graph", "read_indexing_information"}

    context = llm_provider.analyze.call_args.args[0]
    assert context.risk == "LOW"
    assert context.diff_content == ""
    assert context.recent_file_authors == {}
    prompt_variables = context.to_prompt_variables()
    assert prompt_variables["diff_content"] == "Not gathered for this analysis."
    assert prompt_variables["recent_file_authors"] == "Not gathered for this analysis."


async def test_reasoning_log_records_every_decision_including_skips(
    db_session: AsyncSession, indexed_repository_and_pr: tuple[Repository, PullRequest]
) -> None:
    _, pull_request = indexed_repository_and_pr
    changed = [
        ChangedFile(path="src/main/java/com/example/orders/OrderDto.java", status="modified")
    ]
    agent, _ = _agent(db_session, changed, _fake_ai_result())

    investigation = await agent.investigate(pull_request.id)

    # Every step is a real, non-empty decision - a skip is recorded just
    # as explicitly as a tool call, never left implicit.
    for step in investigation.reasoning_log:
        assert step.goal
        assert step.plan
        assert step.decision

    # The final step is the LLM synthesis itself - no tool, just a decision.
    assert investigation.reasoning_log[-1].tool_selected is None
    assert "LLM" in investigation.reasoning_log[-1].decision

    step_numbers = [step.step_number for step in investigation.reasoning_log]
    assert step_numbers == sorted(step_numbers)
