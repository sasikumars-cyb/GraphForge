"""`ImpactAnalysisEngine` end-to-end: real Postgres rows, a real Neo4j
graph (built by the real indexing pipeline against the real
`spring_boot_git_repo` fixture), and a stub `IVersionControlProvider` -
the engine doesn't care where changed files come from, so no network call
is needed to exercise it fully.
"""

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.engine.impact_analysis_engine import (
    ImpactAnalysisEngine,
    RepositoryNotIndexedError,
)
from app.analysis.graph.neo4j_impact_reader import Neo4jImpactGraphReader
from app.core.exceptions import NotFoundError
from app.graph.models import GraphEdge, GraphNode, GraphPayload
from app.graph.neo4j_repository import Neo4jGraphRepository
from app.graph.session import get_driver
from app.indexer.services.indexing_service import index_repository
from app.integrations.interfaces import ChangedFile, IVersionControlProvider
from app.models.pull_request import PullRequest
from app.models.repository import Repository
from app.models.user import User

pytestmark = pytest.mark.asyncio


class StubVersionControlProvider(IVersionControlProvider):
    def __init__(self, changed_files: list[ChangedFile]) -> None:
        self._changed_files = changed_files

    async def get_diff(
        self, owner: str, repo: str, pull_number: int, access_token: str | None = None
    ) -> str:
        raise NotImplementedError

    async def get_recent_file_authors(
        self, owner: str, repo: str, file_paths: set[str], access_token: str | None = None
    ) -> dict[str, list[str]]:
        raise NotImplementedError

    async def get_file_content(
        self, owner: str, repo: str, path: str, access_token: str | None = None
    ) -> str | None:
        raise NotImplementedError

    async def list_changed_files(
        self, owner: str, repo: str, pull_number: int, access_token: str | None = None
    ) -> list[ChangedFile]:
        return self._changed_files


async def _create_repository_and_pull_request(
    db_session: AsyncSession, git_repo_path: Path
) -> tuple[Repository, PullRequest]:
    user = User(email=f"engine-{uuid.uuid4().hex[:8]}@example.com", full_name="Engine Test")
    db_session.add(user)
    await db_session.flush()

    repository = Repository(
        user_id=user.id,
        github_repo_id="1",
        owner="local",
        name="engine-test-repo",
        full_name="local/engine-test-repo",
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


def _engine(db_session: AsyncSession, changed_files: list[ChangedFile]) -> ImpactAnalysisEngine:
    driver = get_driver()
    return ImpactAnalysisEngine(
        db=db_session,
        graph_repository=Neo4jGraphRepository(driver),
        impact_graph_reader=Neo4jImpactGraphReader(driver),
        version_control_provider=StubVersionControlProvider(changed_files),
    )


async def test_kafka_producer_change_is_high_risk_with_downstream_consumer(
    db_session: AsyncSession, indexed_repository_and_pr: tuple[Repository, PullRequest]
) -> None:
    _, pull_request = indexed_repository_and_pr
    changed = [
        ChangedFile(
            path="src/main/java/com/example/orders/OrderEventProducer.java", status="modified"
        )
    ]

    analysis = await _engine(db_session, changed).analyze_pull_request(pull_request.id)

    assert analysis.risk == "HIGH"
    assert {n["name"] for n in analysis.directly_impacted_services} == {"OrderEventProducer"}
    assert {n["name"] for n in analysis.indirectly_impacted_services} == {"OrderEventListener"}
    assert {n["name"] for n in analysis.impacted_topics} == {"order-created", "order-cancelled"}
    assert len(analysis.dependency_paths) == 2


async def test_controller_change_is_medium_risk(
    db_session: AsyncSession, indexed_repository_and_pr: tuple[Repository, PullRequest]
) -> None:
    _, pull_request = indexed_repository_and_pr
    changed = [
        ChangedFile(path="src/main/java/com/example/orders/OrderController.java", status="modified")
    ]

    analysis = await _engine(db_session, changed).analyze_pull_request(pull_request.id)

    assert analysis.risk == "MEDIUM"
    assert {n["name"] for n in analysis.directly_impacted_services} == {"OrderController"}
    assert len(analysis.impacted_apis) == 4


async def test_pom_change_is_high_risk_and_lists_all_dependencies(
    db_session: AsyncSession, indexed_repository_and_pr: tuple[Repository, PullRequest]
) -> None:
    _, pull_request = indexed_repository_and_pr
    changed = [ChangedFile(path="pom.xml", status="modified")]

    analysis = await _engine(db_session, changed).analyze_pull_request(pull_request.id)

    assert analysis.risk == "HIGH"
    assert len(analysis.impacted_libraries) == 4
    assert analysis.directly_impacted_services == []


async def test_dto_only_change_is_low_risk(
    db_session: AsyncSession, indexed_repository_and_pr: tuple[Repository, PullRequest]
) -> None:
    _, pull_request = indexed_repository_and_pr
    changed = [
        ChangedFile(path="src/main/java/com/example/orders/OrderDto.java", status="modified")
    ]

    analysis = await _engine(db_session, changed).analyze_pull_request(pull_request.id)

    assert analysis.risk == "LOW"
    assert analysis.directly_impacted_services == []
    assert analysis.impacted_apis == []
    assert analysis.impacted_topics == []


async def test_feign_client_change_is_high_risk(
    db_session: AsyncSession, indexed_repository_and_pr: tuple[Repository, PullRequest]
) -> None:
    _, pull_request = indexed_repository_and_pr
    changed = [
        ChangedFile(path="src/main/java/com/example/orders/PaymentClient.java", status="modified")
    ]

    analysis = await _engine(db_session, changed).analyze_pull_request(pull_request.id)

    assert analysis.risk == "HIGH"
    assert {n["name"] for n in analysis.directly_impacted_services} == {"PaymentClient"}
    assert len(analysis.impacted_apis) == 2


async def test_feign_caller_repository_is_indirectly_impacted(
    db_session: AsyncSession, indexed_repository_and_pr: tuple[Repository, PullRequest]
) -> None:
    """KAN-19: a repository that reaches this one via a Feign
    `CALLS_SERVICE` edge - real cross-repository data `cross_repo_linker`
    already produces - must show up as indirectly impacted when a
    Component in this repository changes. Hand-writes the caller side
    (a bare `Repository` node plus the cross-repo edge) directly, the same
    pattern `test_cross_repo_linker.py` uses, rather than indexing a
    second full repository - `cross_repo_linker` itself is already covered
    there; this test is only about the impact-analysis read side.
    """
    repository, pull_request = indexed_repository_and_pr
    graph_repository = Neo4jGraphRepository(get_driver())

    caller_repository_id = f"caller-{uuid.uuid4().hex[:8]}"
    caller_repo_node_id = f"{caller_repository_id}:repository"
    target_repo_node_id = f"{repository.id}:repository"

    await graph_repository.replace_repository_graph(
        caller_repository_id,
        GraphPayload(
            nodes=[GraphNode(id=caller_repo_node_id, labels=["Repository"], properties={})]
        ),
    )
    await graph_repository.replace_cross_repository_edges(
        caller_repository_id,
        [
            GraphEdge(
                source_id=caller_repo_node_id,
                target_id=target_repo_node_id,
                type="CALLS_SERVICE",
                properties={
                    "via": ["OrderClient"],
                    "target_name": ["orders-service"],
                    "confidence": "structural",
                },
            )
        ],
    )

    try:
        changed = [
            ChangedFile(
                path="src/main/java/com/example/orders/OrderController.java", status="modified"
            )
        ]
        analysis = await _engine(db_session, changed).analyze_pull_request(pull_request.id)

        indirect_repo_ids = {n["repository_id"] for n in analysis.indirectly_impacted_services}
        assert caller_repository_id in indirect_repo_ids
        assert any(
            step["node_id"] == caller_repo_node_id
            for path in analysis.dependency_paths
            for step in path["steps"]
        )
    finally:
        await graph_repository.replace_repository_graph(caller_repository_id, GraphPayload())


async def test_analysis_without_component_change_skips_service_caller_lookup(
    db_session: AsyncSession, indexed_repository_and_pr: tuple[Repository, PullRequest]
) -> None:
    """A DTO-only change touches no `Component`, so there's nothing for a
    `CALLS_SERVICE` caller to be indirectly impacted *by* - the engine
    should skip the lookup entirely rather than returning an empty result
    from a real query, mirroring the existing `topic_ids` guard."""
    _, pull_request = indexed_repository_and_pr
    changed = [
        ChangedFile(path="src/main/java/com/example/orders/OrderDto.java", status="modified")
    ]

    analysis = await _engine(db_session, changed).analyze_pull_request(pull_request.id)

    assert analysis.indirectly_impacted_services == []


async def test_analysis_is_persisted_and_replaced_on_rerun(
    db_session: AsyncSession, indexed_repository_and_pr: tuple[Repository, PullRequest]
) -> None:
    _, pull_request = indexed_repository_and_pr

    first = await _engine(
        db_session,
        [ChangedFile(path="src/main/java/com/example/orders/OrderDto.java", status="modified")],
    ).analyze_pull_request(pull_request.id)
    second = await _engine(
        db_session, [ChangedFile(path="pom.xml", status="modified")]
    ).analyze_pull_request(pull_request.id)

    assert first.id == second.id
    assert second.risk == "HIGH"


async def test_unindexed_repository_raises(
    db_session: AsyncSession, spring_boot_git_repo: Path
) -> None:
    _, pull_request = await _create_repository_and_pull_request(db_session, spring_boot_git_repo)

    with pytest.raises(RepositoryNotIndexedError):
        await _engine(db_session, []).analyze_pull_request(pull_request.id)


async def test_nonexistent_pull_request_raises(db_session: AsyncSession) -> None:
    with pytest.raises(NotFoundError):
        await _engine(db_session, []).analyze_pull_request(uuid.uuid4())
