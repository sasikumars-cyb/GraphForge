"""ADR 0018 RFC-02B: `index_repository` running the deterministic
hypothesis generator in shadow mode — real local git clone, real
tree-sitter parse, real Neo4j write, matching `test_indexing_pipeline.py`'s
own no-mocks convention. Proves the four things RFC-02B commits to:

- indexing succeeds when the generator succeeds
- indexing succeeds when the generator throws
- the persisted graph payload is unaffected by shadow execution either way
- shadow execution logs what it did (or that it failed), and nothing else
  is observable from outside `index_repository`
"""

import logging
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest

from app.graph.models import GraphPayload
from app.graph.neo4j_repository import Neo4jGraphRepository
from app.graph.session import get_driver
from app.indexer.graph.builder import build_graph
from app.indexer.hypotheses import shadow_runner
from app.indexer.parsers.java.spring_boot_parser import SpringBootJavaParser
from app.indexer.services.indexing_service import index_repository

pytestmark = pytest.mark.asyncio


@pytest.fixture
def repository_id() -> str:
    return f"test-shadow-{uuid.uuid4()}"


@pytest.fixture
async def graph_repository(repository_id: str) -> AsyncGenerator[Neo4jGraphRepository, None]:
    repo = Neo4jGraphRepository(get_driver())
    yield repo
    await repo.replace_repository_graph(repository_id, GraphPayload())


async def test_indexing_succeeds_when_shadow_generation_succeeds(
    spring_boot_git_repo: Path,
    repository_id: str,
    graph_repository: Neo4jGraphRepository,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger="app.indexer.hypotheses.shadow_runner"):
        summary = await index_repository(
            repository_id=repository_id, html_url=str(spring_boot_git_repo), ref="main"
        )

    assert summary["controllers"] == 1
    assert await graph_repository.has_graph(repository_id)

    success_records = [
        r for r in caplog.records if "shadow_hypothesis_generation_succeeded" in r.message
    ]
    assert len(success_records) == 1
    message = success_records[0].message
    assert f"repository_id={repository_id}" in message
    assert "evidence_count=" in message
    assert "hypothesis_count=" in message
    assert "elapsed_seconds=" in message
    # Real evidence: a Spring Boot fixture with a controller, service, Feign
    # client, and Kafka usage produces at least one hypothesis, not zero.
    assert "hypothesis_count=0" not in message


async def test_indexing_succeeds_when_shadow_generation_throws(
    spring_boot_git_repo: Path,
    repository_id: str,
    graph_repository: Neo4jGraphRepository,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("simulated shadow-generation failure")

    monkeypatch.setattr(shadow_runner, "architecture_model_to_evidence_pack", _boom)

    with caplog.at_level(logging.INFO, logger="app.indexer.hypotheses.shadow_runner"):
        summary = await index_repository(
            repository_id=repository_id, html_url=str(spring_boot_git_repo), ref="main"
        )

    # Indexing itself must fully succeed — the whole point of shadow mode.
    assert summary["controllers"] == 1
    assert await graph_repository.has_graph(repository_id)

    failure_records = [
        r for r in caplog.records if "shadow_hypothesis_generation_failed" in r.message
    ]
    assert len(failure_records) == 1
    assert f"repository_id={repository_id}" in failure_records[0].message
    assert failure_records[0].levelno == logging.ERROR
    assert failure_records[0].exc_info is not None


async def test_graph_payload_is_byte_for_byte_identical_with_shadow_generation_enabled(
    spring_boot_git_repo: Path,
    repository_id: str,
    graph_repository: Neo4jGraphRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The strongest form of "no behaviour change": capture the exact
    `GraphPayload` object `index_repository` hands to
    `replace_repository_graph` (with the real, unmocked shadow generator
    running) and assert it equals `build_graph` computed independently,
    completely outside `index_repository` and with no shadow code involved
    at all — proving shadow execution altered nothing about what gets
    persisted, not just that indexing "still works"."""
    captured_graphs: list[GraphPayload] = []
    original_replace = Neo4jGraphRepository.replace_repository_graph

    async def _capture_and_replace(self, repo_id, graph):  # type: ignore[no-untyped-def]
        captured_graphs.append(graph)
        return await original_replace(self, repo_id, graph)

    monkeypatch.setattr(Neo4jGraphRepository, "replace_repository_graph", _capture_and_replace)

    await index_repository(
        repository_id=repository_id, html_url=str(spring_boot_git_repo), ref="main"
    )

    assert len(captured_graphs) == 1
    persisted_graph = captured_graphs[0]

    model = SpringBootJavaParser().parse(spring_boot_git_repo)
    expected_graph = build_graph(repository_id, model)

    assert persisted_graph == expected_graph


async def test_graph_payload_is_identical_whether_or_not_shadow_generation_runs(
    spring_boot_git_repo: Path,
    graph_repository: Neo4jGraphRepository,  # noqa: ARG001 — cleans up `repository_id`'s graph
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Belt-and-braces: index the same fixture twice under two different
    repository ids — once with shadow generation disabled entirely (a
    no-op stand-in for "before RFC-02B"), once with it enabled (the real
    code as of RFC-02B) — and assert the two persisted graphs have
    identical topology once the repository-id namespace is normalized out
    of every node/edge id."""
    repo = Neo4jGraphRepository(get_driver())
    repository_id_before = f"test-shadow-before-{uuid.uuid4()}"
    repository_id_after = f"test-shadow-after-{uuid.uuid4()}"
    try:
        await index_repository(
            repository_id=repository_id_after, html_url=str(spring_boot_git_repo), ref="main"
        )

        async def _no_op_shadow(**kwargs: object) -> None:
            return None

        monkeypatch.setattr(
            "app.indexer.services.indexing_service.run_shadow_hypothesis_generation", _no_op_shadow
        )
        await index_repository(
            repository_id=repository_id_before, html_url=str(spring_boot_git_repo), ref="main"
        )

        graph_before = await repo.get_full_graph(repository_id_before)
        graph_after = await repo.get_full_graph(repository_id_after)

        def _normalized(graph, repository_id: str):
            def strip(id_: str) -> str:
                return id_.replace(repository_id, "REPO", 1)

            nodes = {(strip(n.id), tuple(sorted(n.labels))) for n in graph.nodes}
            edges = {(strip(e.source_id), e.type, strip(e.target_id)) for e in graph.edges}
            return nodes, edges

        assert _normalized(graph_before, repository_id_before) == _normalized(
            graph_after, repository_id_after
        )
    finally:
        await repo.replace_repository_graph(repository_id_before, GraphPayload())
        await repo.replace_repository_graph(repository_id_after, GraphPayload())
