"""Tests for app.indexer.hypotheses.deterministic_generator — ADR 0018
RFC-02.

Two levels of coverage, matching this codebase's existing precedent (see
`test_graph_builder.py` for isolated unit coverage, `test_indexing_pipeline
.py` for real end-to-end fixtures):

1. `TestGeneratorInIsolation` — the `HypothesisGenerator` contract exercised
   against a hand-built `EngineeringEvidencePack`, independent of any
   parser or `build_graph` call.
2. `TestRoundTripAgainstRealFixtures` — the actual success criterion ADR
   0018 states for RFC-02: "100% lossless round-trip on all current
   fixtures," run against the real `spring_boot_sample`/`python_sample`
   fixture repositories through the real parsers.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from app.indexer.graph.builder import build_graph
from app.indexer.hypotheses.deterministic_generator import (
    DeterministicParserHypothesisGenerator,
    architecture_model_to_evidence_pack,
)
from app.indexer.parsers.java.spring_boot_parser import SpringBootJavaParser
from app.indexer.parsers.python.python_parser import PythonParser
from app.knowledge_engine.contracts.evidence import (
    EngineeringEvidencePack,
    EvidenceItem,
    EvidenceReference,
)
from app.knowledge_engine.contracts.provenance import GeneratorIdentity, Provenance

JAVA_FIXTURE_ROOT = Path(__file__).parent.parent.parent / "fixtures" / "spring_boot_sample"
PYTHON_FIXTURE_ROOT = Path(__file__).parent.parent.parent / "fixtures" / "python_sample"


def _edge_topology(hypotheses) -> Counter:
    return Counter((h.source_entity, h.relationship_type, h.target_entity) for h in hypotheses)


def _payload_topology(payload) -> Counter:
    return Counter((e.source_id, e.type, e.target_id) for e in payload.edges)


class TestGeneratorInIsolation:
    """Exercises the `HypothesisGenerator` port directly against a small,
    hand-built pack — no parser, no `build_graph`, no fixture repository."""

    def _pack(self) -> EngineeringEvidencePack:
        identity = GeneratorIdentity(kind="deterministic", name="stub_parser", version="1.0.0")
        provenance = Provenance(
            generator=identity,
            produced_at=datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC),
            pack_id="pack-1",
            pack_version="v1",
            run_id="pack-1",
        )
        node_a = EvidenceItem(
            id="evidence:node:repo:controller:OrderController",
            kind="graph_node:Component:Controller",
            source_type="code",
            reliability_tier=3,
            reference=EvidenceReference(
                repository_id="repo-1",
                source_type="code",
                locator="OrderController.java",
                key="repo-1:controller:com.example.OrderController",
            ),
            raw_value=json.dumps({"name": "OrderController"}),
            provenance=provenance,
        )
        node_b = EvidenceItem(
            id="evidence:node:repo:repository",
            kind="graph_node:Repository",
            source_type="code",
            reliability_tier=3,
            reference=EvidenceReference(
                repository_id="repo-1",
                source_type="code",
                locator="repo-1:repository",
                key="repo-1:repository",
            ),
            raw_value=json.dumps({"language": "java"}),
            provenance=provenance,
        )
        edge = EvidenceItem(
            id="evidence:repo:edge:0:repo-1:repository:CONTAINS:repo-1:controller:com.example.OrderController",
            kind="graph_edge:CONTAINS",
            source_type="code",
            reliability_tier=3,
            reference=EvidenceReference(
                repository_id="repo-1",
                source_type="code",
                locator="repo-1:repository -> repo-1:controller:com.example.OrderController",
                key="repo-1:repository:CONTAINS:repo-1:controller:com.example.OrderController",
            ),
            raw_value=json.dumps(
                {
                    "source_id": "repo-1:repository",
                    "target_id": "repo-1:controller:com.example.OrderController",
                    "type": "CONTAINS",
                    "properties": {},
                }
            ),
            provenance=provenance,
        )
        return EngineeringEvidencePack(
            id="pack-1",
            repository_id="repo-1",
            commit_sha="abc123",
            schema_version="v1",
            items=(node_b, node_a, edge),
        )

    async def test_produces_one_hypothesis_per_edge_item(self):
        generator = DeterministicParserHypothesisGenerator(
            GeneratorIdentity(kind="deterministic", name="stub_parser", version="1.0.0")
        )
        hypotheses = await generator.generate(self._pack())
        assert len(hypotheses) == 1
        hypothesis = hypotheses[0]
        assert hypothesis.relationship_type == "CONTAINS"
        assert hypothesis.source_entity == "repo-1:repository"
        assert hypothesis.target_entity == "repo-1:controller:com.example.OrderController"

    async def test_evidence_refs_cite_the_edge_and_both_endpoint_nodes(self):
        generator = DeterministicParserHypothesisGenerator(
            GeneratorIdentity(kind="deterministic", name="stub_parser", version="1.0.0")
        )
        pack = self._pack()
        hypothesis = (await generator.generate(pack))[0]
        pack_item_ids = {item.id for item in pack.items}
        assert set(hypothesis.evidence_refs).issubset(pack_item_ids)
        assert len(hypothesis.evidence_refs) == 3  # edge + source node + target node

    async def test_empty_pack_produces_no_hypotheses(self):
        generator = DeterministicParserHypothesisGenerator(
            GeneratorIdentity(kind="deterministic", name="stub_parser", version="1.0.0")
        )
        empty_pack = EngineeringEvidencePack(
            id="pack-empty", repository_id="repo-1", commit_sha="abc123", schema_version="v1"
        )
        assert await generator.generate(empty_pack) == []

    async def test_generator_confidence_is_advisory_maximum_never_a_confidence_state(self):
        generator = DeterministicParserHypothesisGenerator(
            GeneratorIdentity(kind="deterministic", name="stub_parser", version="1.0.0")
        )
        hypothesis = (await generator.generate(self._pack()))[0]
        assert hypothesis.generator_confidence == 1.0


class TestRoundTripAgainstRealFixtures:
    """The actual RFC-02 success criterion: for every real fixture
    repository, the generator's hypotheses reconstruct exactly the same
    (source, relationship_type, target) topology `build_graph` itself
    produces from the same `ArchitectureModel` — no edge lost, none
    invented."""

    async def _assert_lossless_round_trip(self, *, repository_id: str, model, generator_name: str):
        identity = GeneratorIdentity(kind="deterministic", name=generator_name, version="1.0.0")

        payload = build_graph(repository_id, model)
        pack = architecture_model_to_evidence_pack(
            repository_id=repository_id, commit_sha="fixture-commit", model=model, identity=identity
        )
        generator = DeterministicParserHypothesisGenerator(identity)
        hypotheses = await generator.generate(pack)

        assert len(hypotheses) == len(payload.edges)
        assert _edge_topology(hypotheses) == _payload_topology(payload)

        # Every evidence_ref a hypothesis cites must actually resolve within
        # the pack it was generated from (RFC-01's ingestion-time invariant,
        # re-verified here for real fixture data, not just hand-built cases).
        pack_item_ids = {item.id for item in pack.items}
        for hypothesis in hypotheses:
            assert set(hypothesis.evidence_refs).issubset(pack_item_ids)

        return hypotheses

    async def test_spring_boot_sample_is_lossless(self):
        model = SpringBootJavaParser().parse(JAVA_FIXTURE_ROOT)
        hypotheses = await self._assert_lossless_round_trip(
            repository_id="repo-java", model=model, generator_name="spring_boot_java_parser"
        )
        assert len(hypotheses) > 0  # the fixture is known to contain real relationships

    async def test_python_sample_is_lossless(self):
        model = PythonParser().parse(PYTHON_FIXTURE_ROOT)
        hypotheses = await self._assert_lossless_round_trip(
            repository_id="repo-python", model=model, generator_name="python_parser"
        )
        assert len(hypotheses) > 0

    async def test_regenerating_from_the_same_model_is_idempotent(self):
        """Reproducibility (ADR 0018): the same model, parsed and adapted
        twice, must yield identical hypothesis ids — not just an
        equivalent-looking topology."""
        model = PythonParser().parse(PYTHON_FIXTURE_ROOT)
        identity = GeneratorIdentity(kind="deterministic", name="python_parser", version="1.0.0")

        pack_1 = architecture_model_to_evidence_pack(
            repository_id="repo-python", commit_sha="fixture-commit", model=model, identity=identity
        )
        pack_2 = architecture_model_to_evidence_pack(
            repository_id="repo-python", commit_sha="fixture-commit", model=model, identity=identity
        )
        generator = DeterministicParserHypothesisGenerator(identity)
        hypotheses_1 = await generator.generate(pack_1)
        hypotheses_2 = await generator.generate(pack_2)

        assert {h.id for h in hypotheses_1} == {h.id for h in hypotheses_2}
