"""ADR 0018 RFC-03's binding success criterion: the new
Hypothesis -> Validator -> ConfidenceEngine pipeline reproduces
`app.indexer.graph.cross_repo_linker.py`'s existing, hand-assigned
`structural`/`heuristic` confidence labels — for every current cross-repo
edge type — using the *same* test fixtures `tests/unit/ai
/test_cross_repo_linker.py` already uses (reused directly, not
re-authored), so this is a genuine regression-parity check against the
production behavior, not a new set of assumptions.

Mapping under test: `structural` -> `HIGHLY_LIKELY` (a single, literal
annotation-match confirmation); `heuristic` -> `LIKELY` (a single,
dependency-coordinate-name confirmation); no match -> no hypothesis at all
(nothing to have a state).
"""

from __future__ import annotations

from app.graph.models import GraphNode
from app.indexer.graph.cross_repo_linker import RepoNodes
from app.knowledge_engine.confidence.default_engine import DefaultConfidenceEngine
from app.knowledge_engine.contracts.confidence import ConfidenceModel, ConfidenceState
from app.knowledge_engine.validators.cross_repo import (
    CROSS_REPO_VALIDATORS,
    build_candidate_pack_and_hypotheses,
)
from app.knowledge_engine.validators.registry import run_validators


def _repo(
    repository_id: str,
    name: str,
    *,
    feign_clients: list[GraphNode] | None = None,
    maven_dependencies: list[GraphNode] | None = None,
    python_dependencies: list[GraphNode] | None = None,
    produces: frozenset[str] = frozenset(),
    consumes: frozenset[str] = frozenset(),
) -> RepoNodes:
    return RepoNodes(
        repository_id=repository_id,
        name=name,
        feign_clients=feign_clients or [],
        maven_dependencies=maven_dependencies or [],
        python_dependencies=python_dependencies or [],
        produces_topic_names=produces,
        consumes_topic_names=consumes,
    )


async def _confidence_for(source: RepoNodes, other: RepoNodes) -> dict[str, ConfidenceModel]:
    """The full pipeline, end to end, for one repository pair: build
    candidates -> validate each independently -> aggregate. Returns one
    `ConfidenceModel` per relationship_type that produced a hypothesis."""
    pack, hypotheses = build_candidate_pack_and_hypotheses(source, other)
    engine = DefaultConfidenceEngine()
    result: dict[str, ConfidenceModel] = {}
    for hypothesis in hypotheses:
        results = await run_validators(hypothesis, pack, CROSS_REPO_VALIDATORS)
        model: ConfidenceModel | None = None
        for validation_result in results:
            model = engine.aggregate(model, validation_result)
        assert model is not None  # every hypothesis here has >=1 applicable validator
        result[hypothesis.relationship_type] = model
    return result


class TestFeignServiceCallsParity:
    async def test_match_reproduces_structural_as_highly_likely(self):
        feign_node = GraphNode(
            id="repo-a:feign:x.EtlCoreClient",
            labels=["Component", "FeignClient"],
            properties={"name": "EtlCoreClient", "target_name": "etl-core-service"},
        )
        source = _repo("repo-a", "ingestion-framework", feign_clients=[feign_node])
        other = _repo("repo-b", "etl-core")

        confidence = await _confidence_for(source, other)

        assert confidence["CALLS_SERVICE"].state == ConfidenceState.HIGHLY_LIKELY

    async def test_no_match_produces_no_hypothesis(self):
        feign_node = GraphNode(
            id="repo-a:feign:x.UnrelatedClient",
            labels=["Component", "FeignClient"],
            properties={"name": "UnrelatedClient", "target_name": "some-other-service"},
        )
        source = _repo("repo-a", "ingestion-framework", feign_clients=[feign_node])
        other = _repo("repo-b", "etl-core")

        confidence = await _confidence_for(source, other)

        assert "CALLS_SERVICE" not in confidence


class TestKafkaTopicOverlapParity:
    async def test_shared_topic_reproduces_structural_as_highly_likely(self):
        source = _repo("repo-a", "ingestion-framework", produces={"orders-created"})
        other = _repo("repo-b", "etl-core", consumes={"orders-created"})

        confidence = await _confidence_for(source, other)

        assert confidence["SHARES_TOPIC"].state == ConfidenceState.HIGHLY_LIKELY

    async def test_multiple_shared_topics_still_reproduces_structural(self):
        source = _repo(
            "repo-a", "ingestion-framework", produces={"orders-created", "orders-updated"}
        )
        other = _repo("repo-b", "etl-core", consumes={"orders-created", "orders-updated"})

        confidence = await _confidence_for(source, other)

        assert confidence["SHARES_TOPIC"].state == ConfidenceState.HIGHLY_LIKELY

    async def test_no_shared_topic_produces_no_hypothesis(self):
        source = _repo("repo-a", "ingestion-framework", produces={"orders-created"})
        other = _repo("repo-b", "etl-core", consumes={"unrelated-topic"})

        confidence = await _confidence_for(source, other)

        assert "SHARES_TOPIC" not in confidence


class TestSharedDependencyNameParity:
    async def test_maven_match_reproduces_heuristic_as_likely(self):
        dep_node = GraphNode(
            id="repo-a:dependency:com.acme:etl-core",
            labels=["MavenDependency"],
            properties={"group_id": "com.acme", "artifact_id": "etl-core"},
        )
        source = _repo("repo-a", "ingestion-framework", maven_dependencies=[dep_node])
        other = _repo("repo-b", "etl-core")

        confidence = await _confidence_for(source, other)

        assert confidence["DEPENDS_ON_REPOSITORY"].state == ConfidenceState.LIKELY

    async def test_python_match_reproduces_heuristic_as_likely(self):
        dep_node = GraphNode(
            id="repo-a:python-dependency:etl-core",
            labels=["PythonDependency"],
            properties={"name": "etl-core"},
        )
        source = _repo("repo-a", "ingestion-framework", python_dependencies=[dep_node])
        other = _repo("repo-b", "etl-core")

        confidence = await _confidence_for(source, other)

        assert confidence["DEPENDS_ON_REPOSITORY"].state == ConfidenceState.LIKELY

    async def test_no_match_produces_no_hypothesis(self):
        dep_node = GraphNode(
            id="repo-a:dependency:org.apache:commons-lang3",
            labels=["MavenDependency"],
            properties={"group_id": "org.apache", "artifact_id": "commons-lang3"},
        )
        source = _repo("repo-a", "ingestion-framework", maven_dependencies=[dep_node])
        other = _repo("repo-b", "etl-core")

        confidence = await _confidence_for(source, other)

        assert "DEPENDS_ON_REPOSITORY" not in confidence


class TestStructuralIsAlwaysMoreTrustedThanHeuristic:
    """The one property that matters most: for the same repository pair,
    a structural-class match must never end up at or below a heuristic-
    class match's confidence state."""

    async def test_highly_likely_outranks_likely(self):
        feign_node = GraphNode(
            id="repo-a:feign:x.EtlCoreClient",
            labels=["Component", "FeignClient"],
            properties={"name": "EtlCoreClient", "target_name": "etl-core-service"},
        )
        dep_node = GraphNode(
            id="repo-a:dependency:com.acme:etl-core",
            labels=["MavenDependency"],
            properties={"group_id": "com.acme", "artifact_id": "etl-core"},
        )
        source = _repo(
            "repo-a",
            "ingestion-framework",
            feign_clients=[feign_node],
            maven_dependencies=[dep_node],
        )
        other = _repo("repo-b", "etl-core")

        confidence = await _confidence_for(source, other)

        states = list(ConfidenceState)
        assert states.index(confidence["CALLS_SERVICE"].state) < states.index(
            confidence["DEPENDS_ON_REPOSITORY"].state
        )
