"""`app.decision.build_from_pull_request.build_decision` — real Postgres for
the relationship-confidence half (same pattern as
`test_engineering_intelligence_impact_analysis_service.py`), a fake
`IGraphRepository`/`IImpactGraphReader` for the graph half.

Covers the three outcomes that matter for the wedge: a confirmed
cross-repository impact producing `approve`, an unconfirmed one producing an
`OpenQuestion` plus `request_changes` rather than a fabricated confidence, and
a change that matches no indexed node at all.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.graph.interfaces import IImpactGraphReader
from app.analysis.graph.models import TraversalHop
from app.decision.build_from_pull_request import build_decision
from app.decision.contracts import DiffStat
from app.graph.interfaces import IGraphRepository
from app.graph.models import GraphEdge, GraphNode, GraphPayload
from app.knowledge_engine.contracts.confidence import ConfidenceModel, ConfidenceState
from app.knowledge_engine.contracts.knowledge import KnowledgeRelationship
from app.knowledge_engine.contracts.provenance import GeneratorIdentity, Provenance
from app.knowledge_engine.memory_service import EngineeringMemoryService
from app.models.repository import Repository
from app.models.user import User

pytestmark = pytest.mark.asyncio


class _FakeGraphRepository(IGraphRepository):
    """Same fake shape as `test_engineering_intelligence_impact_analysis_service
    .py`'s — `build_decision` never talks to Neo4j directly, only through
    `compute_blast_radius`, so only `get_neighborhood` needs a real body."""

    def __init__(self, neighborhood: GraphPayload) -> None:
        self._neighborhood = neighborhood

    async def replace_repository_graph(self, repository_id: str, graph: GraphPayload) -> None:
        raise NotImplementedError

    async def replace_repository_files_subgraph(
        self, repository_id: str, file_paths: list[str], graph: GraphPayload
    ) -> None:
        raise NotImplementedError

    async def get_full_graph(
        self, repository_id: str, *, limit: int | None = None, node_types: list[str] | None = None
    ) -> GraphPayload:
        raise NotImplementedError

    async def get_nodes_by_label(self, repository_id: str, label: str) -> list[GraphNode]:
        raise NotImplementedError

    async def get_kafka_topic_edges(self, repository_id: str) -> list[GraphEdge]:
        raise NotImplementedError

    async def has_graph(self, repository_id: str) -> bool:
        raise NotImplementedError

    async def replace_cross_repository_edges(
        self, source_repository_id: str, edges: list[GraphEdge]
    ) -> None:
        raise NotImplementedError

    async def get_outgoing_cross_repository_edges(self, repository_id: str) -> list[GraphEdge]:
        raise NotImplementedError

    async def get_neighborhood(
        self, repository_id: str, seed_node_ids: list[str], edge_types: list[str], max_hops: int
    ) -> GraphPayload:
        return self._neighborhood


class _FakeImpactGraphReader(IImpactGraphReader):
    """Only `find_nodes_by_file_paths` has real behavior — that is the only
    method `build_decision` calls directly; every downstream hop goes through
    `compute_blast_radius`, which only needs `IGraphRepository`."""

    def __init__(self, nodes_by_file: dict[str, GraphNode]) -> None:
        self._nodes_by_file = nodes_by_file

    async def find_nodes_by_file_paths(
        self, repository_id: str, file_paths: set[str]
    ) -> list[GraphNode]:
        return [self._nodes_by_file[path] for path in file_paths if path in self._nodes_by_file]

    async def find_downstream_apis(
        self, repository_id: str, node_ids: set[str]
    ) -> list[TraversalHop]:
        raise NotImplementedError

    async def find_downstream_topics(
        self, repository_id: str, node_ids: set[str]
    ) -> list[TraversalHop]:
        raise NotImplementedError

    async def find_same_repository_topic_peers(
        self, repository_id: str, topic_ids: set[str], exclude_node_ids: set[str]
    ) -> list[TraversalHop]:
        raise NotImplementedError

    async def find_cross_repository_topic_peers(
        self, topic_names: set[str], allowed_repository_ids: set[str]
    ) -> list[TraversalHop]:
        raise NotImplementedError

    async def get_dependencies(self, repository_id: str) -> list[GraphNode]:
        raise NotImplementedError

    async def find_cross_repository_service_callers(self, repository_id: str) -> list[TraversalHop]:
        raise NotImplementedError


async def _make_repository(db_session: AsyncSession, *, name: str = "test-repo") -> uuid.UUID:
    user = User(email=f"test-{uuid.uuid4()}@example.com", full_name="Test User")
    db_session.add(user)
    await db_session.flush()
    repo = Repository(
        user_id=user.id,
        owner="test-owner",
        name=name,
        full_name=f"test-owner/{name}",
        html_url=f"https://github.com/test-owner/{name}",
        default_branch="main",
        source="github",
        github_repo_id=str(uuid.uuid4().int)[:10],
    )
    db_session.add(repo)
    await db_session.flush()
    return repo.id


def _confidence(state: ConfidenceState, *, sources: int = 2) -> ConfidenceModel:
    names = frozenset(sorted({"code", "openapi", "runtime_telemetry"})[:sources])
    return ConfidenceModel(
        state=state,
        distinct_confirming_source_types=len(names),
        confirming_source_types=names,
        max_confirming_reliability_tier=2 if names else 0,
        contradiction_count=1 if state == ConfidenceState.CONFLICTING else 0,
        computed_at=datetime(2026, 8, 6, tzinfo=UTC),
        formula_version="1.0.0",
    )


async def _store_relationship(
    db_session: AsyncSession,
    repository_id: uuid.UUID,
    *,
    relationship_type: str,
    source_entity: str,
    target_entity: str,
    confidence: ConfidenceModel,
) -> None:
    memory = EngineeringMemoryService(db_session)
    await memory.store_relationship(
        repository_id,
        KnowledgeRelationship(
            id=f"rel-{uuid.uuid4()}",
            relationship_type=relationship_type,
            source_entity=source_entity,
            target_entity=target_entity,
            confidence=confidence,
            hypothesis_ids=("hyp-1",),
            provenance=(
                Provenance(
                    generator=GeneratorIdentity(kind="deterministic", name="test", version="1.0.0"),
                    produced_at=datetime(2026, 8, 6, tzinfo=UTC),
                    pack_id="pack-1",
                    pack_version="v1",
                    run_id="run-1",
                ),
            ),
        ),
    )


def _diff_stat() -> DiffStat:
    return DiffStat(files_changed=1, lines_added=12, lines_removed=3)


class TestConfirmedCrossRepositoryImpact:
    async def test_a_verified_dependency_produces_an_approve_verdict(
        self, db_session: AsyncSession
    ) -> None:
        origin_id = await _make_repository(db_session, name="billing")
        downstream_id = await _make_repository(db_session, name="inventory")

        origin_node_id = f"{origin_id}:feign:InventoryClient"
        downstream_node_id = f"{downstream_id}:Repository:inventory"

        # The origin repository's own indexing run asserted this dependency -
        # stored under the origin, exactly as `compute_blast_radius`'s own
        # `all_involved_repository_ids` logic expects to find it.
        await _store_relationship(
            db_session,
            origin_id,
            relationship_type="DEPENDS_ON_REPOSITORY",
            source_entity=origin_node_id,
            target_entity=downstream_node_id,
            confidence=_confidence(ConfidenceState.VERIFIED),
        )

        neighborhood = GraphPayload(
            nodes=[
                GraphNode(id=origin_node_id, labels=["FeignClient"]),
                GraphNode(
                    id=downstream_node_id, labels=["Repository"], properties={"name": "inventory"}
                ),
            ],
            edges=[
                GraphEdge(
                    source_id=origin_node_id,
                    target_id=downstream_node_id,
                    type="DEPENDS_ON_REPOSITORY",
                )
            ],
        )
        impact_graph_reader = _FakeImpactGraphReader(
            {"src/InventoryClient.java": GraphNode(id=origin_node_id, labels=["FeignClient"])}
        )

        decision = await build_decision(
            db=db_session,
            graph_repository=_FakeGraphRepository(neighborhood),
            impact_graph_reader=impact_graph_reader,
            repository_id=str(origin_id),
            pull_request_id="pr-1",
            commit_sha="abc123",
            changed_files=["src/InventoryClient.java"],
            diff_stat=_diff_stat(),
        )

        assert decision.merge_recommendation.verdict == "approve"
        assert len(decision.affected_entities) == 1
        entity = decision.affected_entities[0]
        assert entity.entity_id == downstream_node_id
        assert entity.entity_name == "inventory"
        assert entity.entity_type == "service"
        assert entity.origin == "deterministic"
        assert entity.confidence.state == ConfidenceState.VERIFIED
        assert entity.confidence.distinct_confirming_source_types == 2
        assert decision.open_questions == ()
        assert decision.reviewer_actions == ()

    async def test_relationship_path_records_the_real_traversed_edge(
        self, db_session: AsyncSession
    ) -> None:
        origin_id = await _make_repository(db_session, name="billing")
        downstream_id = await _make_repository(db_session, name="inventory")
        origin_node_id = f"{origin_id}:feign:InventoryClient"
        downstream_node_id = f"{downstream_id}:Repository:inventory"

        await _store_relationship(
            db_session,
            origin_id,
            relationship_type="DEPENDS_ON_REPOSITORY",
            source_entity=origin_node_id,
            target_entity=downstream_node_id,
            confidence=_confidence(ConfidenceState.HIGHLY_LIKELY),
        )
        neighborhood = GraphPayload(
            nodes=[
                GraphNode(id=origin_node_id, labels=["FeignClient"]),
                GraphNode(
                    id=downstream_node_id, labels=["Repository"], properties={"name": "inventory"}
                ),
            ],
            edges=[
                GraphEdge(
                    source_id=origin_node_id,
                    target_id=downstream_node_id,
                    type="DEPENDS_ON_REPOSITORY",
                )
            ],
        )
        decision = await build_decision(
            db=db_session,
            graph_repository=_FakeGraphRepository(neighborhood),
            impact_graph_reader=_FakeImpactGraphReader(
                {"src/InventoryClient.java": GraphNode(id=origin_node_id, labels=["FeignClient"])}
            ),
            repository_id=str(origin_id),
            pull_request_id="pr-1",
            commit_sha="abc123",
            changed_files=["src/InventoryClient.java"],
            diff_stat=_diff_stat(),
        )

        entity = decision.affected_entities[0]
        assert len(entity.relationship_path) == 1
        assert entity.relationship_path[0].edge_type == "DEPENDS_ON_REPOSITORY"
        assert entity.relationship_path[0].from_node_id == origin_node_id
        assert entity.relationship_path[0].to_node_id == downstream_node_id


class TestUnconfirmedImpactIsAnOpenQuestionNotAFabricatedScore:
    async def test_structural_edge_with_no_confidence_record_becomes_an_open_question(
        self, db_session: AsyncSession
    ) -> None:
        origin_id = await _make_repository(db_session, name="billing")
        downstream_id = await _make_repository(db_session, name="inventory")
        origin_node_id = f"{origin_id}:feign:InventoryClient"
        downstream_node_id = f"{downstream_id}:Repository:inventory"

        # No _store_relationship call: the traversal finds the structural
        # edge, but the Materializer has never computed a ConfidenceModel
        # for it.
        neighborhood = GraphPayload(
            nodes=[
                GraphNode(id=origin_node_id, labels=["FeignClient"]),
                GraphNode(
                    id=downstream_node_id, labels=["Repository"], properties={"name": "inventory"}
                ),
            ],
            edges=[
                GraphEdge(
                    source_id=origin_node_id,
                    target_id=downstream_node_id,
                    type="DEPENDS_ON_REPOSITORY",
                )
            ],
        )
        decision = await build_decision(
            db=db_session,
            graph_repository=_FakeGraphRepository(neighborhood),
            impact_graph_reader=_FakeImpactGraphReader(
                {"src/InventoryClient.java": GraphNode(id=origin_node_id, labels=["FeignClient"])}
            ),
            repository_id=str(origin_id),
            pull_request_id="pr-1",
            commit_sha="abc123",
            changed_files=["src/InventoryClient.java"],
            diff_stat=_diff_stat(),
        )

        assert decision.affected_entities == ()
        assert len(decision.open_questions) == 1
        question = decision.open_questions[0]
        assert question.related_entity_id == downstream_node_id
        assert question.safety_relevant is True
        assert "no confidence" in question.why_unknown
        # Not swept into an unconditional pass: an unresolved safety-relevant
        # unknown must hold the verdict back.
        assert decision.merge_recommendation.verdict == "request_changes"

    async def test_candidate_confidence_produces_a_blocking_reviewer_action(
        self, db_session: AsyncSession
    ) -> None:
        origin_id = await _make_repository(db_session, name="billing")
        downstream_id = await _make_repository(db_session, name="inventory")
        origin_node_id = f"{origin_id}:feign:InventoryClient"
        downstream_node_id = f"{downstream_id}:Repository:inventory"

        await _store_relationship(
            db_session,
            origin_id,
            relationship_type="DEPENDS_ON_REPOSITORY",
            source_entity=origin_node_id,
            target_entity=downstream_node_id,
            confidence=_confidence(ConfidenceState.CANDIDATE, sources=1),
        )
        neighborhood = GraphPayload(
            nodes=[
                GraphNode(id=origin_node_id, labels=["FeignClient"]),
                GraphNode(
                    id=downstream_node_id, labels=["Repository"], properties={"name": "inventory"}
                ),
            ],
            edges=[
                GraphEdge(
                    source_id=origin_node_id,
                    target_id=downstream_node_id,
                    type="DEPENDS_ON_REPOSITORY",
                )
            ],
        )
        decision = await build_decision(
            db=db_session,
            graph_repository=_FakeGraphRepository(neighborhood),
            impact_graph_reader=_FakeImpactGraphReader(
                {"src/InventoryClient.java": GraphNode(id=origin_node_id, labels=["FeignClient"])}
            ),
            repository_id=str(origin_id),
            pull_request_id="pr-1",
            commit_sha="abc123",
            changed_files=["src/InventoryClient.java"],
            diff_stat=_diff_stat(),
        )

        assert len(decision.affected_entities) == 1
        assert len(decision.reviewer_actions) == 1
        action = decision.reviewer_actions[0]
        assert action.blocking is True
        assert action.target == "inventory"
        assert decision.merge_recommendation.verdict == "approve_with_conditions"
        assert decision.merge_recommendation.blocking_conditions == (action.action_id,)


class TestRejectedEntitiesDoNotAskForConfirmation:
    async def test_rejected_confidence_needs_no_reviewer_action(
        self, db_session: AsyncSession
    ) -> None:
        origin_id = await _make_repository(db_session, name="billing")
        downstream_id = await _make_repository(db_session, name="notifications")
        origin_node_id = f"{origin_id}:feign:NotificationsClient"
        downstream_node_id = f"{downstream_id}:Repository:notifications"

        await _store_relationship(
            db_session,
            origin_id,
            relationship_type="DEPENDS_ON_REPOSITORY",
            source_entity=origin_node_id,
            target_entity=downstream_node_id,
            confidence=_confidence(ConfidenceState.REJECTED, sources=0),
        )
        neighborhood = GraphPayload(
            nodes=[
                GraphNode(id=origin_node_id, labels=["FeignClient"]),
                GraphNode(
                    id=downstream_node_id,
                    labels=["Repository"],
                    properties={"name": "notifications"},
                ),
            ],
            edges=[
                GraphEdge(
                    source_id=origin_node_id,
                    target_id=downstream_node_id,
                    type="DEPENDS_ON_REPOSITORY",
                )
            ],
        )
        decision = await build_decision(
            db=db_session,
            graph_repository=_FakeGraphRepository(neighborhood),
            impact_graph_reader=_FakeImpactGraphReader(
                {
                    "src/NotificationsClient.java": GraphNode(
                        id=origin_node_id, labels=["FeignClient"]
                    )
                }
            ),
            repository_id=str(origin_id),
            pull_request_id="pr-1",
            commit_sha="abc123",
            changed_files=["src/NotificationsClient.java"],
            diff_stat=_diff_stat(),
        )

        assert len(decision.affected_entities) == 1
        assert decision.affected_entities[0].confidence.state == ConfidenceState.REJECTED
        assert decision.reviewer_actions == ()
        assert decision.merge_recommendation.verdict == "approve"


class TestNoIndexedNodeMatched:
    async def test_change_touching_nothing_indexed_approves_with_a_non_safety_question(
        self, db_session: AsyncSession
    ) -> None:
        origin_id = await _make_repository(db_session, name="billing")
        decision = await build_decision(
            db=db_session,
            graph_repository=_FakeGraphRepository(GraphPayload()),
            impact_graph_reader=_FakeImpactGraphReader({}),
            repository_id=str(origin_id),
            pull_request_id="pr-1",
            commit_sha="abc123",
            changed_files=["README.md"],
            diff_stat=_diff_stat(),
        )

        assert decision.affected_entities == ()
        assert decision.change_summary.capabilities_touched == ()
        assert len(decision.open_questions) == 1
        assert decision.open_questions[0].safety_relevant is False
        assert decision.merge_recommendation.verdict == "approve"


class TestDecisionIdIsContentAddressed:
    async def test_same_pull_request_and_commit_produce_the_same_decision_id(
        self, db_session: AsyncSession
    ) -> None:
        origin_id = await _make_repository(db_session, name="billing")
        kwargs = dict(
            db=db_session,
            graph_repository=_FakeGraphRepository(GraphPayload()),
            impact_graph_reader=_FakeImpactGraphReader({}),
            repository_id=str(origin_id),
            pull_request_id="pr-1",
            commit_sha="abc123",
            changed_files=["README.md"],
            diff_stat=_diff_stat(),
        )
        first = await build_decision(**kwargs)  # type: ignore[arg-type]
        second = await build_decision(**kwargs)  # type: ignore[arg-type]
        assert first.decision_id == second.decision_id

    async def test_a_different_commit_produces_a_different_decision_id(
        self, db_session: AsyncSession
    ) -> None:
        origin_id = await _make_repository(db_session, name="billing")
        first = await build_decision(
            db=db_session,
            graph_repository=_FakeGraphRepository(GraphPayload()),
            impact_graph_reader=_FakeImpactGraphReader({}),
            repository_id=str(origin_id),
            pull_request_id="pr-1",
            commit_sha="abc123",
            changed_files=["README.md"],
            diff_stat=_diff_stat(),
        )
        second = await build_decision(
            db=db_session,
            graph_repository=_FakeGraphRepository(GraphPayload()),
            impact_graph_reader=_FakeImpactGraphReader({}),
            repository_id=str(origin_id),
            pull_request_id="pr-1",
            commit_sha="def456",
            changed_files=["README.md"],
            diff_stat=_diff_stat(),
        )
        assert first.decision_id != second.decision_id
