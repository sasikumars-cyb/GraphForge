"""Impact correctness (C-2, P0.4, P0.5, M-2).

The audit found three linked defects, all visible in one real answer:

- `impacted_repositories` contained the seed repository itself, so every
  blast radius reported at least one "downstream" impact and `_severity`'s
  "low" band was unreachable — five sampled repositories all reported
  "medium" with only themselves in the list.
- The structured lists and the prose `why` were built from different
  sources, so `why` could name a downstream repository and two databases
  that `affected_repositories`/`affected_databases` did not contain.
- Nothing bounded the result before it reached the prompt.

These tests pin the invariants rather than the wording.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

from app.graph.models import GraphEdge, GraphNode, GraphPayload
from app.services import ask_grounding as ag
from app.services.engineering_intelligence.contracts import BlastRadius, EntityReference

SEED_UUID = uuid.uuid4()
SEED_NODE = f"{SEED_UUID}:repository"


def _repo(name: str = "seed-service"):
    return SimpleNamespace(id=SEED_UUID, name=name, full_name=f"acme/{name}")


def _blast(
    *,
    repositories: tuple[str, ...] = (),
    databases: tuple[str, ...] = (),
    apis: tuple[str, ...] = (),
    queues: tuple[str, ...] = (),
    edges: tuple[GraphEdge, ...] = (),
) -> BlastRadius:
    return BlastRadius(
        seed=EntityReference(repository_id=str(SEED_UUID), node_id=SEED_NODE),
        direction="downstream",
        max_hops=2,
        impacted_repositories=repositories,
        impacted_apis=apis,
        impacted_databases=databases,
        impacted_queues=queues,
        subgraph=GraphPayload(nodes=(), edges=edges),
    )


class TestSeverity:
    def test_zero_downstream_is_low(self):
        """Unreachable before the seed was excluded from its own blast
        radius — every repository scored at least 1."""
        facts = ag.build_impact_facts(_blast(), _repo(), {})
        assert facts.downstream_total == 0
        assert facts.severity == "low"

    def test_one_and_two_downstream_are_medium(self):
        one = ag.build_impact_facts(_blast(repositories=("a:repository",)), _repo(), {})
        two = ag.build_impact_facts(
            _blast(repositories=("a:repository", "b:repository")), _repo(), {}
        )
        assert one.severity == "medium"
        assert two.severity == "medium"

    def test_three_or_more_downstream_is_high(self):
        facts = ag.build_impact_facts(
            _blast(repositories=("a:repository", "b:repository", "c:repository")), _repo(), {}
        )
        assert facts.severity == "high"

    def test_severity_counts_every_kind_not_just_repositories(self):
        facts = ag.build_impact_facts(
            _blast(repositories=("a:repository",), databases=("d1",), queues=("q1",)),
            _repo(),
            {},
        )
        assert facts.downstream_total == 3
        assert facts.severity == "high"

    def test_severity_is_deterministic_and_matches_the_structured_counts(self):
        blast = _blast(repositories=("a:repository", "b:repository"), apis=("e1",))
        first = ag.build_impact_facts(blast, _repo(), {})
        second = ag.build_impact_facts(blast, _repo(), {})
        assert first.severity == second.severity
        assert first.severity == ag._severity(first.downstream_total)


class TestStructuredAndProseAgree:
    def test_key_paths_only_name_entities_from_the_same_traversal(self):
        """The prose is built from the traversal's own edges, so it can no
        longer name a repository or database absent from the structured
        lists."""
        edges = (
            GraphEdge(source_id=SEED_NODE, target_id="other:repository", type="DEPENDS_ON"),
            GraphEdge(source_id=SEED_NODE, target_id="db-1", type="READS_FROM"),
        )
        facts = ag.build_impact_facts(
            _blast(repositories=("other:repository",), databases=("db-1",), edges=edges),
            _repo(),
            {"other:repository": "acme/other", "db-1": "Ledger"},
        )
        assert "acme/other" in facts.affected_repositories
        assert "Ledger" in facts.affected_databases
        joined = " ".join(facts.key_paths)
        assert "acme/other" in joined
        assert "Ledger" in joined

    def test_no_edges_means_no_key_paths_rather_than_invented_ones(self):
        facts = ag.build_impact_facts(_blast(), _repo(), {})
        assert facts.key_paths == []


class TestBounding:
    def test_affected_lists_are_capped_and_flagged(self):
        many = tuple(f"repo-{i}:repository" for i in range(40))
        facts = ag.build_impact_facts(_blast(repositories=many), _repo(), {})
        assert len(facts.affected_repositories) == ag._MAX_AFFECTED_PER_KIND
        assert facts.truncated is True

    def test_key_paths_are_capped(self):
        edges = tuple(
            GraphEdge(source_id=SEED_NODE, target_id=f"n{i}", type="CALLS") for i in range(20)
        )
        facts = ag.build_impact_facts(_blast(edges=edges), _repo(), {})
        assert len(facts.key_paths) == ag._MAX_KEY_PATHS
        assert facts.truncated is True

    def test_a_small_result_is_not_marked_truncated(self):
        facts = ag.build_impact_facts(
            _blast(
                repositories=("a:repository",),
                edges=(
                    GraphEdge(source_id=SEED_NODE, target_id="a:repository", type="DEPENDS_ON"),
                ),
            ),
            _repo(),
            {},
        )
        assert facts.truncated is False


class TestSeedExclusionAtTheDomainLayer:
    """`compute_blast_radius` is where the seed is dropped — proven here
    against the real function with a stubbed graph, so the invariant holds
    for every caller (Ask, PR decisions, change simulation), not just Ask."""

    async def test_seed_repository_is_never_its_own_downstream_impact(self):
        import app.services.engineering_intelligence.impact_analysis_service as svc

        seed_node = GraphNode(
            id=SEED_NODE, labels=["Repository"], properties={"name": "seed-service"}
        )
        other = GraphNode(
            id=f"{uuid.uuid4()}:repository", labels=["Repository"], properties={"name": "other"}
        )

        class _Traversal:
            payload = GraphPayload(nodes=(seed_node, other), edges=())
            nodes_by_label: dict[str, tuple[str, ...]] = {}

        async def _fake_traverse(*_args, **_kwargs):
            return _Traversal()

        async def _no_relationships(*_args, **_kwargs):
            return []

        original_traverse = svc.graph_traversal.traverse
        original_lookup = svc.relationship_lookup.fetch_with_confidence
        svc.graph_traversal.traverse = _fake_traverse  # type: ignore[assignment]
        svc.relationship_lookup.fetch_with_confidence = _no_relationships  # type: ignore[assignment]
        try:
            radius = await svc.compute_blast_radius(
                db=None,  # type: ignore[arg-type]
                graph_repository=None,  # type: ignore[arg-type]
                entity=EntityReference(repository_id=str(SEED_UUID), node_id=SEED_NODE),
            )
        finally:
            svc.graph_traversal.traverse = original_traverse  # type: ignore[assignment]
            svc.relationship_lookup.fetch_with_confidence = original_lookup  # type: ignore[assignment]

        assert SEED_NODE not in radius.impacted_repositories
        assert radius.impacted_repositories == (other.id,)
