"""ADR 0018 RFC-03A — validator coverage for the deterministic generator's
own relationship types (`CONTAINS`, `EXPOSES`, `CALLS`, `PRODUCES_TO`,
`CONSUMES_FROM`, `DEPENDS_ON`, `IMPORTS`, `INHERITS_FROM`, `READS_FROM`,
`WRITES_TO`) — the gap the RFC-03 readiness assessment found: the
validator registry covered only cross-repository relationship types,
leaving every single-repository hypothesis this platform has ever
generated unvalidated.

Reuse discipline: these validators read the *same* `graph_node:*`/
`graph_edge:*` evidence items `architecture_model_to_evidence_pack`
already produces (RFC-02) — no new extraction logic, no re-implementation
of `graph/builder.py`'s node/edge derivation (ambiguous Python call-name
resolution, cross-module base-class resolution, the generic-component
fallback all stay exactly where they are, untouched). Node lookup reuses
`app.indexer.hypotheses.deterministic_generator.node_evidence_items_by_node_id`
rather than re-deriving it a second time.

Independence, not circularity: a validator here does not merely confirm
"the edge evidence item exists" (which would be tautological — RFC-02's
pack construction guarantees that for anything it produces). Each
validator instead checks a *second*, independent fact about the
endpoints' own recorded properties — grounded location, HTTP-method
shape, dependency-coordinate well-formedness, non-self-reference — so a
genuinely malformed or missing fact is actually catchable, not merely
re-asserted.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from app.indexer.hypotheses.deterministic_generator import node_evidence_items_by_node_id
from app.knowledge_engine.contracts.evidence import EngineeringEvidencePack
from app.knowledge_engine.contracts.hypothesis import Hypothesis
from app.knowledge_engine.contracts.provenance import GeneratorIdentity, Provenance
from app.knowledge_engine.contracts.validation import KnowledgeValidator, ValidationResult

_DETERMINISTIC_TIER = 3

_ALL_DETERMINISTIC_RELATIONSHIP_TYPES = frozenset(
    {
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
    }
)

_VALID_HTTP_METHODS = frozenset({"GET", "POST", "PUT", "DELETE", "PATCH", "ANY"})

# Not every node kind `graph/builder.py` produces carries a `file_path`/
# `name` property — verified directly against that module's own
# `GraphNode(..., properties={...})` calls, not assumed:
#   - `Repository` has no file_path/name at all (it represents the whole
#     repository, not a file within it) — its own id is its grounding.
#   - `MavenDependency` has `group_id`/`artifact_id`, never `name` or
#     `file_path`.
#   - Every other kind this validator encounters (Controller, Service,
#     FeignClient, Module, Class, Function, generic Component, Endpoint,
#     KafkaTopic, PythonDependency, DataTable) does carry `name` and/or
#     `file_path`.
# Getting this wrong (treating the general case as universal) previously
# caused every CONTAINS-from-Repository and every DEPENDS_ON-to-
# MavenDependency hypothesis to be wrongly `contradicts`ed against real,
# well-formed fixture data — caught by
# `TestFullCoverageAgainstRealFixtures`, not assumed away.
_REPOSITORY_NODE_KIND = "graph_node:Repository"
_MAVEN_DEPENDENCY_NODE_KIND = "graph_node:MavenDependency"


def _is_grounded(item_kind: str, properties: dict) -> bool:
    if item_kind == _REPOSITORY_NODE_KIND:
        return True
    if item_kind == _MAVEN_DEPENDENCY_NODE_KIND:
        return bool(properties.get("group_id") or properties.get("artifact_id"))
    return bool(properties.get("file_path") or properties.get("name"))


# A relationship type where a node being its own source *and* target is a
# structural impossibility, not a legitimate self-reference — e.g. a class
# cannot inherit from itself. Deliberately narrow: `CONTAINS` (a repo
# containing itself makes no sense either, but never occurs by
# construction) and `IMPORTS` (a module self-importing is unusual but not
# nonsensical the way self-inheritance is) are left out rather than
# guessed at.
_SELF_REFERENCE_IS_IMPOSSIBLE_FOR = frozenset({"INHERITS_FROM"})


def _provenance(pack_id: str, identity: GeneratorIdentity) -> Provenance:
    return Provenance(
        generator=identity,
        produced_at=datetime.now(UTC),
        pack_id=pack_id,
        pack_version="v1",
        run_id=pack_id,
    )


class StructuralEndpointExistenceValidator(KnowledgeValidator):
    """Applies to every deterministic relationship type. Independently
    re-derives confirmation by checking that *both* endpoints have a
    corresponding `graph_node:*` evidence item, each carrying a non-empty
    grounding location (`file_path` or `name` property) — not merely that
    the edge claim exists, but that the two things it claims to relate are
    themselves independently evidenced.

    Contradicts (RFC-03A Part 3, "referenced component missing") when
    either endpoint has no corresponding node evidence in the pack at
    all — a genuine structural inconsistency: a relationship whose
    source or target was never independently observed. Also contradicts
    for a structurally impossible self-reference (see
    `_SELF_REFERENCE_IS_IMPOSSIBLE_FOR`).
    """

    name = "structural_endpoint_existence"
    applies_to = _ALL_DETERMINISTIC_RELATIONSHIP_TYPES

    async def validate(
        self, hypothesis: Hypothesis, pack: EngineeringEvidencePack
    ) -> ValidationResult:
        identity = GeneratorIdentity(kind="deterministic", name=self.name, version="1.0.0")
        provenance = _provenance(pack.id, identity)

        # RFC-07 hardening: this validator's "both endpoints exist ->
        # confirms at tier 3" logic was only ever sound for a hypothesis
        # whose own claim already came from a deterministic AST parser -
        # there, endpoint existence is independent corroboration of an
        # already-strong, parser-derived fact. `IMPORTS`/`CALLS`/
        # `DEPENDS_ON` are also the generic-language fallback's relationship
        # vocabulary (see `generic_structural.py`), and this validator's
        # `applies_to` is matched purely by relationship_type, not by which
        # generator produced the hypothesis — so it was unintentionally
        # granting a bare "A exists, B exists" LLM hypothesis the exact
        # same tier-3 confirmation a real parser finding gets, letting
        # endpoint existence alone reach VERIFIED. A hypothesis whose own
        # `generator_confidence` is advisory-only and whose claim was never
        # deterministically derived gets no opinion from this validator at
        # all — `generic_structural.py`'s own, deliberately weaker
        # `EndpointExistenceValidator` (tier 1) is what covers that case.
        if hypothesis.provenance.generator.kind != "deterministic":
            return ValidationResult(
                hypothesis_id=hypothesis.id,
                validator_name=self.name,
                verdict="no_signal",
                evidence_used=(),
                source_type="structural_endpoint_existence",
                evidence_reliability_tier=0,
                explanation=(
                    "hypothesis was not produced by the deterministic parser generator - "
                    "this validator's endpoint-existence-as-corroboration logic does not "
                    "apply outside that context"
                ),
                provenance=provenance,
            )

        node_evidence_by_id = node_evidence_items_by_node_id(pack)
        source_item = node_evidence_by_id.get(hypothesis.source_entity)
        target_item = node_evidence_by_id.get(hypothesis.target_entity)

        if source_item is None or target_item is None:
            missing = []
            if source_item is None:
                missing.append(hypothesis.source_entity)
            if target_item is None:
                missing.append(hypothesis.target_entity)
            return ValidationResult(
                hypothesis_id=hypothesis.id,
                validator_name=self.name,
                verdict="contradicts",
                evidence_used=tuple(hypothesis.evidence_refs),
                source_type="structural_endpoint_existence",
                evidence_reliability_tier=_DETERMINISTIC_TIER,
                explanation=f"Referenced component(s) missing from evidence pack: {missing}.",
                provenance=provenance,
            )

        if (
            hypothesis.relationship_type in _SELF_REFERENCE_IS_IMPOSSIBLE_FOR
            and hypothesis.source_entity == hypothesis.target_entity
        ):
            return ValidationResult(
                hypothesis_id=hypothesis.id,
                validator_name=self.name,
                verdict="contradicts",
                evidence_used=(source_item.id,),
                source_type="structural_endpoint_existence",
                evidence_reliability_tier=_DETERMINISTIC_TIER,
                explanation=(
                    f"{hypothesis.relationship_type} claims {hypothesis.source_entity!r} "
                    "relates to itself, which is structurally impossible for this "
                    "relationship type."
                ),
                provenance=provenance,
            )

        source_props = json.loads(source_item.raw_value)
        target_props = json.loads(target_item.raw_value)
        source_grounded = _is_grounded(source_item.kind, source_props)
        target_grounded = _is_grounded(target_item.kind, target_props)

        if not (source_grounded and target_grounded):
            ungrounded = []
            if not source_grounded:
                ungrounded.append(hypothesis.source_entity)
            if not target_grounded:
                ungrounded.append(hypothesis.target_entity)
            return ValidationResult(
                hypothesis_id=hypothesis.id,
                validator_name=self.name,
                verdict="contradicts",
                evidence_used=(source_item.id, target_item.id),
                source_type="structural_endpoint_existence",
                evidence_reliability_tier=_DETERMINISTIC_TIER,
                explanation=(f"Component(s) exist but carry no grounding location: {ungrounded}."),
                provenance=provenance,
            )

        return ValidationResult(
            hypothesis_id=hypothesis.id,
            validator_name=self.name,
            verdict="confirms",
            evidence_used=(source_item.id, target_item.id),
            source_type="structural_endpoint_existence",
            evidence_reliability_tier=_DETERMINISTIC_TIER,
            explanation=(
                "Both endpoints independently evidenced in the pack, each with a "
                "real grounding location."
            ),
            provenance=provenance,
        )


class HttpMethodShapeValidator(KnowledgeValidator):
    """Applies to `EXPOSES`/`CALLS` (both point at an `Endpoint` node in
    `graph/builder.py`'s vocabulary). Independently checks the target
    node's own `http_method` property against the fixed set of real HTTP
    verbs this codebase's extractors ever record
    (`app.indexer.extractors.controllers`/`feign_clients`), rather than
    trusting the hypothesis's claim that this is a legitimate endpoint
    relationship.

    Contradicts (RFC-03A Part 3, "invalid target") when `http_method` is
    present but not a recognized value — a structurally impossible
    endpoint. `no_signal` when the target isn't `Endpoint`-shaped at all
    (defensive; `EXPOSES`/`CALLS` always target an `Endpoint` node in the
    current generator's output, but this validator makes no assumption
    about who its caller is).
    """

    name = "http_method_shape"
    applies_to = frozenset({"EXPOSES", "CALLS"})

    async def validate(
        self, hypothesis: Hypothesis, pack: EngineeringEvidencePack
    ) -> ValidationResult:
        node_evidence_by_id = node_evidence_items_by_node_id(pack)
        identity = GeneratorIdentity(kind="deterministic", name=self.name, version="1.0.0")
        provenance = _provenance(pack.id, identity)

        target_item = node_evidence_by_id.get(hypothesis.target_entity)
        if target_item is None:
            return ValidationResult(
                hypothesis_id=hypothesis.id,
                validator_name=self.name,
                verdict="no_signal",
                evidence_used=(),
                source_type="http_method_shape",
                evidence_reliability_tier=0,
                explanation="Target node not present in the pack; nothing to check.",
                provenance=provenance,
            )

        target_props = json.loads(target_item.raw_value)
        http_method = target_props.get("http_method")
        if http_method is None:
            return ValidationResult(
                hypothesis_id=hypothesis.id,
                validator_name=self.name,
                verdict="no_signal",
                evidence_used=(),
                source_type="http_method_shape",
                evidence_reliability_tier=0,
                explanation="Target node is not Endpoint-shaped (no http_method property).",
                provenance=provenance,
            )

        if http_method not in _VALID_HTTP_METHODS:
            return ValidationResult(
                hypothesis_id=hypothesis.id,
                validator_name=self.name,
                verdict="contradicts",
                evidence_used=(target_item.id,),
                source_type="http_method_shape",
                evidence_reliability_tier=_DETERMINISTIC_TIER,
                explanation=f"{http_method!r} is not a recognized HTTP method.",
                provenance=provenance,
            )

        return ValidationResult(
            hypothesis_id=hypothesis.id,
            validator_name=self.name,
            verdict="confirms",
            evidence_used=(target_item.id,),
            source_type="http_method_shape",
            evidence_reliability_tier=_DETERMINISTIC_TIER,
            explanation=f"Target endpoint's http_method {http_method!r} is a recognized verb.",
            provenance=provenance,
        )


class DependencyCoordinateWellFormedValidator(KnowledgeValidator):
    """Applies to `DEPENDS_ON`. Independently checks the target dependency
    node's own coordinate fields (Maven `group_id`/`artifact_id`, or
    Python `name`) rather than trusting the hypothesis's claim that this
    is a real dependency.

    Contradicts (RFC-03A Part 3, "impossible dependency") when the target
    is dependency-shaped (has the right property keys) but every
    coordinate field is empty — a malformed, impossible-to-resolve
    dependency coordinate.
    """

    name = "dependency_coordinate_well_formed"
    applies_to = frozenset({"DEPENDS_ON"})

    async def validate(
        self, hypothesis: Hypothesis, pack: EngineeringEvidencePack
    ) -> ValidationResult:
        identity = GeneratorIdentity(kind="deterministic", name=self.name, version="1.0.0")
        provenance = _provenance(pack.id, identity)

        # RFC-07 hardening: same reasoning as
        # `StructuralEndpointExistenceValidator` - `is_python_shaped` below
        # only means "has a `name` property," which every generic-fallback
        # `SourceFile`/`GenericSymbol` node also carries incidentally, not
        # because it's a genuine dependency coordinate. Left unguarded,
        # this validator would confirm at tier 3 for ANY generic DEPENDS_ON
        # hypothesis whose target merely exists - restoring exactly the
        # "endpoint existence alone reaches VERIFIED" flaw this cycle set
        # out to fix, just through a second validator. Scope back to the
        # deterministic generator's own hypotheses, which this validator's
        # shape-checking logic actually assumes.
        if hypothesis.provenance.generator.kind != "deterministic":
            return ValidationResult(
                hypothesis_id=hypothesis.id,
                validator_name=self.name,
                verdict="no_signal",
                evidence_used=(),
                source_type="dependency_coordinate_well_formed",
                evidence_reliability_tier=0,
                explanation=(
                    "hypothesis was not produced by the deterministic parser generator - "
                    "this validator's Maven/Python dependency-coordinate shape checks do "
                    "not apply outside that context"
                ),
                provenance=provenance,
            )

        node_evidence_by_id = node_evidence_items_by_node_id(pack)
        target_item = node_evidence_by_id.get(hypothesis.target_entity)
        if target_item is None:
            return ValidationResult(
                hypothesis_id=hypothesis.id,
                validator_name=self.name,
                verdict="no_signal",
                evidence_used=(),
                source_type="dependency_coordinate_well_formed",
                evidence_reliability_tier=0,
                explanation="Target dependency node not present in the pack.",
                provenance=provenance,
            )

        target_props = json.loads(target_item.raw_value)
        is_maven_shaped = "group_id" in target_props or "artifact_id" in target_props
        is_python_shaped = "name" in target_props and not is_maven_shaped

        if is_maven_shaped:
            well_formed = bool(target_props.get("group_id")) and bool(
                target_props.get("artifact_id")
            )
        elif is_python_shaped:
            well_formed = bool(target_props.get("name"))
        else:
            return ValidationResult(
                hypothesis_id=hypothesis.id,
                validator_name=self.name,
                verdict="no_signal",
                evidence_used=(),
                source_type="dependency_coordinate_well_formed",
                evidence_reliability_tier=0,
                explanation="Target node is not dependency-shaped.",
                provenance=provenance,
            )

        if not well_formed:
            return ValidationResult(
                hypothesis_id=hypothesis.id,
                validator_name=self.name,
                verdict="contradicts",
                evidence_used=(target_item.id,),
                source_type="dependency_coordinate_well_formed",
                evidence_reliability_tier=_DETERMINISTIC_TIER,
                explanation="Dependency coordinate is empty/malformed — impossible dependency.",
                provenance=provenance,
            )

        return ValidationResult(
            hypothesis_id=hypothesis.id,
            validator_name=self.name,
            verdict="confirms",
            evidence_used=(target_item.id,),
            source_type="dependency_coordinate_well_formed",
            evidence_reliability_tier=_DETERMINISTIC_TIER,
            explanation="Dependency coordinate is well-formed.",
            provenance=provenance,
        )


DETERMINISTIC_STRUCTURAL_VALIDATORS: tuple[KnowledgeValidator, ...] = (
    StructuralEndpointExistenceValidator(),
    HttpMethodShapeValidator(),
    DependencyCoordinateWellFormedValidator(),
)
