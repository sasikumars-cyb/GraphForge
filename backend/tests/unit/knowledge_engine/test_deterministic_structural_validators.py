"""Tests for app.knowledge_engine.validators.deterministic_structural —
ADR 0018 RFC-03A Parts 1 and 3.

Covers each validator in isolation (hand-built packs, mirroring
`test_cross_repo_parity.py`'s style) plus a real-fixture pass proving the
generator's own hypotheses (RFC-02) now clear a validator for every
relationship type it can produce.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

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
from app.knowledge_engine.contracts.hypothesis import Hypothesis
from app.knowledge_engine.contracts.provenance import GeneratorIdentity, Provenance
from app.knowledge_engine.validators.deterministic_structural import (
    _ALL_DETERMINISTIC_RELATIONSHIP_TYPES,
    DETERMINISTIC_STRUCTURAL_VALIDATORS,
    DependencyCoordinateWellFormedValidator,
    HttpMethodShapeValidator,
    StructuralEndpointExistenceValidator,
)
from app.knowledge_engine.validators.registry import run_validators

JAVA_FIXTURE_ROOT = Path(__file__).parent.parent.parent / "fixtures" / "spring_boot_sample"
PYTHON_FIXTURE_ROOT = Path(__file__).parent.parent.parent / "fixtures" / "python_sample"


def _identity(name: str = "test") -> GeneratorIdentity:
    return GeneratorIdentity(kind="deterministic", name=name, version="1.0.0")


def _provenance(pack_id: str = "pack-1") -> Provenance:
    return Provenance(
        generator=_identity(),
        produced_at=datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC),
        pack_id=pack_id,
        pack_version="v1",
        run_id=pack_id,
    )


def _node_item(node_id: str, kind: str, properties: dict) -> EvidenceItem:
    return EvidenceItem(
        id=f"evidence:node:{node_id}",
        kind=kind,
        source_type="code",
        reliability_tier=3,
        reference=EvidenceReference(
            repository_id="repo-1", source_type="code", locator=node_id, key=node_id
        ),
        raw_value=json.dumps(properties),
        provenance=_provenance(),
    )


def _hypothesis(
    rel_type: str, source: str, target: str, evidence_refs: tuple[str, ...]
) -> Hypothesis:
    return Hypothesis(
        id=f"hyp:{rel_type}:{source}:{target}",
        relationship_type=rel_type,
        source_entity=source,
        target_entity=target,
        evidence_refs=evidence_refs,
        explanation="test hypothesis",
        provenance=_provenance(),
    )


class TestStructuralEndpointExistenceValidator:
    async def test_confirms_when_both_endpoints_grounded(self):
        source_node = _node_item(
            "repo-1:repository", "graph_node:Repository", {"language": "python"}
        )
        target_node = _node_item(
            "repo-1:module:app",
            "graph_node:Component:Module",
            {"name": "app", "file_path": "app.py"},
        )
        pack = EngineeringEvidencePack(
            id="pack-1",
            repository_id="repo-1",
            commit_sha="abc",
            schema_version="v1",
            items=(source_node, target_node),
        )
        hypothesis = _hypothesis(
            "CONTAINS", "repo-1:repository", "repo-1:module:app", ("evidence-1",)
        )

        validator = StructuralEndpointExistenceValidator()
        result = await validator.validate(hypothesis, pack)

        assert result.verdict == "confirms"
        assert result.evidence_reliability_tier == 3

    async def test_contradicts_when_target_missing_from_pack(self):
        source_node = _node_item(
            "repo-1:repository", "graph_node:Repository", {"language": "python"}
        )
        pack = EngineeringEvidencePack(
            id="pack-1",
            repository_id="repo-1",
            commit_sha="abc",
            schema_version="v1",
            items=(source_node,),
        )
        hypothesis = _hypothesis(
            "CONTAINS", "repo-1:repository", "repo-1:module:nonexistent", ("evidence-1",)
        )

        validator = StructuralEndpointExistenceValidator()
        result = await validator.validate(hypothesis, pack)

        assert result.verdict == "contradicts"
        assert "missing" in result.explanation.lower()

    async def test_contradicts_when_endpoint_has_no_grounding_location(self):
        source_node = _node_item(
            "repo-1:repository", "graph_node:Repository", {"language": "python"}
        )
        target_node = _node_item(
            "repo-1:module:app", "graph_node:Component:Module", {}
        )  # no name/file_path
        pack = EngineeringEvidencePack(
            id="pack-1",
            repository_id="repo-1",
            commit_sha="abc",
            schema_version="v1",
            items=(source_node, target_node),
        )
        hypothesis = _hypothesis(
            "CONTAINS", "repo-1:repository", "repo-1:module:app", ("evidence-1",)
        )

        validator = StructuralEndpointExistenceValidator()
        result = await validator.validate(hypothesis, pack)

        assert result.verdict == "contradicts"

    async def test_contradicts_self_inherits_from(self):
        node = _node_item("repo-1:class:app.Foo", "graph_node:Component:Class", {"name": "Foo"})
        pack = EngineeringEvidencePack(
            id="pack-1",
            repository_id="repo-1",
            commit_sha="abc",
            schema_version="v1",
            items=(node,),
        )
        hypothesis = _hypothesis(
            "INHERITS_FROM", "repo-1:class:app.Foo", "repo-1:class:app.Foo", ("evidence-1",)
        )

        validator = StructuralEndpointExistenceValidator()
        result = await validator.validate(hypothesis, pack)

        assert result.verdict == "contradicts"
        assert "itself" in result.explanation.lower()

    async def test_applies_to_covers_all_ten_relationship_types(self):
        assert {
            "CONTAINS",
            "EXPOSES",
            "CALLS",
            "PRODUCES_TO",
            "CONSUMES_FROM",
            "DEPENDS_ON",
            "IMPORTS",
            "INHERITS_FROM",
            "READS_FROM",
            "WRITES_TO",
        } == _ALL_DETERMINISTIC_RELATIONSHIP_TYPES
        validator = StructuralEndpointExistenceValidator()
        assert validator.applies_to == _ALL_DETERMINISTIC_RELATIONSHIP_TYPES


class TestHttpMethodShapeValidator:
    async def test_confirms_valid_http_method(self):
        endpoint_node = _node_item(
            "repo-1:endpoint:GET:/orders:list", "graph_node:Endpoint", {"http_method": "GET"}
        )
        pack = EngineeringEvidencePack(
            id="pack-1",
            repository_id="repo-1",
            commit_sha="abc",
            schema_version="v1",
            items=(endpoint_node,),
        )
        hypothesis = _hypothesis(
            "EXPOSES", "repo-1:controller:x", "repo-1:endpoint:GET:/orders:list", ("evidence-1",)
        )

        result = await HttpMethodShapeValidator().validate(hypothesis, pack)
        assert result.verdict == "confirms"

    async def test_contradicts_invalid_http_method(self):
        endpoint_node = _node_item(
            "repo-1:endpoint:FOO:/orders:list", "graph_node:Endpoint", {"http_method": "FOOBAR"}
        )
        pack = EngineeringEvidencePack(
            id="pack-1",
            repository_id="repo-1",
            commit_sha="abc",
            schema_version="v1",
            items=(endpoint_node,),
        )
        hypothesis = _hypothesis(
            "EXPOSES", "repo-1:controller:x", "repo-1:endpoint:FOO:/orders:list", ("evidence-1",)
        )

        result = await HttpMethodShapeValidator().validate(hypothesis, pack)
        assert result.verdict == "contradicts"

    async def test_no_signal_when_target_not_endpoint_shaped(self):
        other_node = _node_item("repo-1:module:app", "graph_node:Component:Module", {"name": "app"})
        pack = EngineeringEvidencePack(
            id="pack-1",
            repository_id="repo-1",
            commit_sha="abc",
            schema_version="v1",
            items=(other_node,),
        )
        hypothesis = _hypothesis(
            "EXPOSES", "repo-1:controller:x", "repo-1:module:app", ("evidence-1",)
        )

        result = await HttpMethodShapeValidator().validate(hypothesis, pack)
        assert result.verdict == "no_signal"


class TestDependencyCoordinateWellFormedValidator:
    async def test_confirms_well_formed_maven_coordinate(self):
        dep_node = _node_item(
            "repo-1:dependency:com.acme:x",
            "graph_node:MavenDependency",
            {"group_id": "com.acme", "artifact_id": "x"},
        )
        pack = EngineeringEvidencePack(
            id="pack-1",
            repository_id="repo-1",
            commit_sha="abc",
            schema_version="v1",
            items=(dep_node,),
        )
        hypothesis = _hypothesis(
            "DEPENDS_ON", "repo-1:repository", "repo-1:dependency:com.acme:x", ("evidence-1",)
        )

        result = await DependencyCoordinateWellFormedValidator().validate(hypothesis, pack)
        assert result.verdict == "confirms"

    async def test_contradicts_empty_maven_coordinate(self):
        dep_node = _node_item(
            "repo-1:dependency:x", "graph_node:MavenDependency", {"group_id": "", "artifact_id": ""}
        )
        pack = EngineeringEvidencePack(
            id="pack-1",
            repository_id="repo-1",
            commit_sha="abc",
            schema_version="v1",
            items=(dep_node,),
        )
        hypothesis = _hypothesis(
            "DEPENDS_ON", "repo-1:repository", "repo-1:dependency:x", ("evidence-1",)
        )

        result = await DependencyCoordinateWellFormedValidator().validate(hypothesis, pack)
        assert result.verdict == "contradicts"

    async def test_confirms_well_formed_python_coordinate(self):
        dep_node = _node_item(
            "repo-1:python-dependency:requests", "graph_node:PythonDependency", {"name": "requests"}
        )
        pack = EngineeringEvidencePack(
            id="pack-1",
            repository_id="repo-1",
            commit_sha="abc",
            schema_version="v1",
            items=(dep_node,),
        )
        hypothesis = _hypothesis(
            "DEPENDS_ON", "repo-1:repository", "repo-1:python-dependency:requests", ("evidence-1",)
        )

        result = await DependencyCoordinateWellFormedValidator().validate(hypothesis, pack)
        assert result.verdict == "confirms"

    async def test_contradicts_empty_python_coordinate(self):
        dep_node = _node_item(
            "repo-1:python-dependency:x", "graph_node:PythonDependency", {"name": ""}
        )
        pack = EngineeringEvidencePack(
            id="pack-1",
            repository_id="repo-1",
            commit_sha="abc",
            schema_version="v1",
            items=(dep_node,),
        )
        hypothesis = _hypothesis(
            "DEPENDS_ON", "repo-1:repository", "repo-1:python-dependency:x", ("evidence-1",)
        )

        result = await DependencyCoordinateWellFormedValidator().validate(hypothesis, pack)
        assert result.verdict == "contradicts"


class TestFullCoverageAgainstRealFixtures:
    """Part 4's coverage requirement, proven directly: every relationship
    type the real generator produces from a real fixture clears at least
    one validator with a `confirms` or `contradicts` verdict (never
    silently zero applicable validators) — and, since real fixture data is
    internally consistent, well-formed deterministic-parser output, every
    verdict must actually be `confirms`. A `contradicts` here would mean
    either a validator bug or genuinely malformed fixture data — this is
    the exact assertion that would have caught the Repository-node
    grounding bug found and fixed during this RFC without needing the
    separate hand-built unit test to notice it."""

    async def _hypotheses_for(self, parser, path: Path, identity_name: str) -> list[Hypothesis]:
        model = parser.parse(path)
        identity = GeneratorIdentity(kind="deterministic", name=identity_name, version="1.0.0")
        pack = architecture_model_to_evidence_pack(
            repository_id="repo-1", commit_sha="fixture", model=model, identity=identity
        )
        generator = DeterministicParserHypothesisGenerator(identity)
        hypotheses = await generator.generate(pack)
        return pack, hypotheses

    async def _assert_full_confirmed_coverage(self, pack, hypotheses) -> None:
        assert (
            hypotheses
        ), "fixture must produce at least one hypothesis for this test to mean anything"
        for h in hypotheses:
            applicable = [
                v
                for v in DETERMINISTIC_STRUCTURAL_VALIDATORS
                if h.relationship_type in v.applies_to
            ]
            assert applicable, f"no validator applies to {h.relationship_type}"
            results = await run_validators(h, pack, DETERMINISTIC_STRUCTURAL_VALIDATORS)
            assert results, f"no validator produced a result for {h.relationship_type}"
            contradictions = [r for r in results if r.verdict == "contradicts"]
            assert not contradictions, (
                f"unexpected contradiction(s) against well-formed fixture data for "
                f"{h.relationship_type} ({h.source_entity} -> {h.target_entity}): {contradictions}"
            )
            assert any(
                r.verdict == "confirms" for r in results
            ), f"no validator confirmed {h.relationship_type} against well-formed fixture data"

    async def test_every_java_fixture_hypothesis_has_an_applicable_validator(self):
        pack, hypotheses = await self._hypotheses_for(
            SpringBootJavaParser(), JAVA_FIXTURE_ROOT, "spring_boot_java_parser"
        )
        await self._assert_full_confirmed_coverage(pack, hypotheses)

    async def test_every_python_fixture_hypothesis_has_an_applicable_validator(self):
        pack, hypotheses = await self._hypotheses_for(
            PythonParser(), PYTHON_FIXTURE_ROOT, "python_parser"
        )
        await self._assert_full_confirmed_coverage(pack, hypotheses)
