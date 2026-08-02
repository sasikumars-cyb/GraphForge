"""ADR 0018 RFC-03 — proving the validator/confidence-engine machinery
against `app.indexer.graph.cross_repo_linker.py`'s existing, hand-assigned
`structural`/`heuristic` confidence vocabulary.

Reuse, not reimplementation: hypothesis *candidacy* is determined by
calling `cross_repo_linker.py`'s own rule functions (`_feign_service_calls`
/`_kafka_topic_overlap`/`_shared_dependency_name`) directly — the exact
same tested, deterministic matching logic already in production, not a
second copy of it. What's new here is *independent* confirmation: each
validator re-derives its verdict from the same underlying primitives
(`_identifier_match`, literal topic-name equality) applied to the raw
evidence in the pack, rather than trusting the hypothesis's own claim or
reading back `cross_repo_linker.py`'s already-computed `confidence`
string — that string is deliberately never read here, since reproducing
it (not copying it) is RFC-03's whole point.

Reliability tiers formalize reasoning `cross_repo_linker.py` already
documents in its own docstrings: a literal `@FeignClient`/Kafka-topic match
is "structural" (`_STRUCTURAL_TIER`, same value as RFC-02's deterministic-
parser tier); a dependency-coordinate name match is "a real but heuristic
signal, not a structural one" (`_HEURISTIC_TIER`) — see
`_shared_dependency_name`'s own docstring in `cross_repo_linker.py`.

`commit_sha="n/a-cross-repo"`: cross-repository linking compares multiple
already-indexed repositories at once, not one repository's evidence at one
commit — there is no single commit this candidate pack is "about." Same
kind of honest placeholder as RFC-02B's `ref`-as-`commit_sha` stand-in, for
the same reason: this data isn't persisted or read by anything outside
this pack yet.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from app.indexer.graph.cross_repo_linker import (
    CROSS_REPO_LINK_RULES,
    RepoNodes,
    _identifier_match,
)
from app.knowledge_engine.contracts.evidence import (
    EngineeringEvidencePack,
    EvidenceItem,
    EvidenceReference,
)
from app.knowledge_engine.contracts.hypothesis import Hypothesis
from app.knowledge_engine.contracts.provenance import GeneratorIdentity, Provenance
from app.knowledge_engine.contracts.validation import KnowledgeValidator, ValidationResult

_STRUCTURAL_TIER = 3
_HEURISTIC_TIER = 1

_CANDIDATE_IDENTITY = GeneratorIdentity(
    kind="deterministic", name="cross_repo_link_candidate", version="1.0.0"
)


def _provenance(pack_id: str, generator: GeneratorIdentity) -> Provenance:
    return Provenance(
        generator=generator,
        produced_at=datetime.now(UTC),
        pack_id=pack_id,
        pack_version="v1",
        run_id=pack_id,
    )


def build_candidate_pack_and_hypotheses(
    source: RepoNodes, other: RepoNodes
) -> tuple[EngineeringEvidencePack, list[Hypothesis]]:
    """For one ordered pair of repositories, run every `CROSS_REPO_LINK_RULES`
    rule to find candidates (reused, unmodified), and build the raw-fact
    evidence + `Hypothesis` for each — with the rule's own `confidence`
    property deliberately discarded, since that's exactly what independent
    validation (below) is meant to re-derive, not copy.
    """
    pack_id = f"pack:cross_repo:{source.repository_id}:{other.repository_id}"
    provenance = _provenance(pack_id, _CANDIDATE_IDENTITY)

    items: list[EvidenceItem] = []
    hypotheses: list[Hypothesis] = []

    rule_names = {rule.name for rule in CROSS_REPO_LINK_RULES}
    assert rule_names == {"feign_service_calls", "kafka_topic_overlap", "shared_dependency_name"}, (
        "cross_repo_linker.CROSS_REPO_LINK_RULES changed shape — this module's "
        "reuse of it needs a matching update, not a silent mismatch"
    )

    if (result := _feign_candidate(source, other, provenance)) is not None:
        hyp, evidence = result
        hypotheses.append(hyp)
        items.extend(evidence)

    if (result := _kafka_candidate(source, other, provenance)) is not None:
        hyp, evidence = result
        hypotheses.append(hyp)
        items.extend(evidence)

    if (result := _dependency_candidate(source, other, provenance)) is not None:
        hyp, evidence = result
        hypotheses.append(hyp)
        items.extend(evidence)

    # Evidence items are content-addressed by construction (see each
    # builder below) but the same topic-usage fact can legitimately be
    # produced for both the source and target side — dedupe by id, same
    # discipline `graph/builder.py`'s own `deduped_nodes` already uses.
    deduped_items = list({item.id: item for item in items}.values())

    pack = EngineeringEvidencePack(
        id=pack_id,
        repository_id=source.repository_id,
        commit_sha="n/a-cross-repo",
        schema_version="v1",
        items=tuple(deduped_items),
    )
    return pack, hypotheses


def _feign_candidate(
    source: RepoNodes, other: RepoNodes, provenance: Provenance
) -> tuple[Hypothesis, list[EvidenceItem]] | None:
    rule = next(r for r in CROSS_REPO_LINK_RULES if r.name == "feign_service_calls")
    edges = rule.find_edges(source, other)
    if not edges:
        return None
    edge = edges[0]

    evidence: list[EvidenceItem] = []
    for index, (target_name, via) in enumerate(
        zip(edge.properties["target_name"], edge.properties["via"], strict=True)
    ):
        evidence.append(
            EvidenceItem(
                id=f"evidence:{source.repository_id}:{other.repository_id}:feign_target_match:{index}",
                kind="feign_client_target_match",
                source_type="code",
                reliability_tier=_STRUCTURAL_TIER,
                reference=EvidenceReference(
                    repository_id=source.repository_id,
                    source_type="code",
                    locator=f"feign_client:{target_name}",
                    key=target_name,
                ),
                # `via` (the Feign client's own `name` property) is carried
                # here purely so RFC-06's materializer can reconstruct
                # `CALLS_SERVICE`'s `via` edge property without re-reading
                # source code — never read by `FeignTargetLiteralValidator`
                # below, which still only re-derives its verdict from
                # `target_name`/`other_repository_name`.
                raw_value=json.dumps(
                    {
                        "target_name": target_name,
                        "other_repository_name": other.name,
                        "via": via,
                    }
                ),
                provenance=provenance,
            )
        )

    hypothesis = Hypothesis(
        id=f"hyp:cross_repo_link_candidate:CALLS_SERVICE:{source.repository_id}:{other.repository_id}",
        relationship_type="CALLS_SERVICE",
        source_entity=edge.source_id,
        target_entity=edge.target_id,
        evidence_refs=tuple(item.id for item in evidence),
        explanation=(
            f"A Feign client in {source.repository_id!r} names a target "
            f"matching {other.name!r}."
        ),
        provenance=provenance,
    )
    return hypothesis, evidence


def _kafka_candidate(
    source: RepoNodes, other: RepoNodes, provenance: Provenance
) -> tuple[Hypothesis, list[EvidenceItem]] | None:
    rule = next(r for r in CROSS_REPO_LINK_RULES if r.name == "kafka_topic_overlap")
    edges = rule.find_edges(source, other)
    if not edges:
        return None
    edge = edges[0]

    # Atomic evidence: every topic *each side* produces/consumes — not
    # just the shared ones — so the validator can genuinely recompute the
    # intersection itself rather than being handed the answer.
    evidence: list[EvidenceItem] = []
    for repo, topics, direction in (
        (source, source.produces_topic_names, "produces"),
        (source, source.consumes_topic_names, "consumes"),
        (other, other.produces_topic_names, "produces"),
        (other, other.consumes_topic_names, "consumes"),
    ):
        for topic in sorted(topics):
            evidence.append(
                EvidenceItem(
                    id=f"evidence:{repo.repository_id}:kafka:{direction}:{topic}",
                    kind="kafka_topic_usage",
                    source_type="code",
                    reliability_tier=_STRUCTURAL_TIER,
                    reference=EvidenceReference(
                        repository_id=repo.repository_id,
                        source_type="code",
                        locator=f"kafka:{direction}:{topic}",
                        key=topic,
                    ),
                    raw_value=json.dumps(
                        {
                            "repository_id": repo.repository_id,
                            "topic": topic,
                            "direction": direction,
                        }
                    ),
                    provenance=provenance,
                )
            )

    hypothesis = Hypothesis(
        id=f"hyp:cross_repo_link_candidate:SHARES_TOPIC:{source.repository_id}:{other.repository_id}",
        relationship_type="SHARES_TOPIC",
        source_entity=edge.source_id,
        target_entity=edge.target_id,
        evidence_refs=tuple(sorted({item.id for item in evidence})),
        explanation=(
            f"{source.repository_id!r} and {other.repository_id!r} share at "
            "least one Kafka topic literal."
        ),
        provenance=provenance,
    )
    return hypothesis, evidence


def _dependency_candidate(
    source: RepoNodes, other: RepoNodes, provenance: Provenance
) -> tuple[Hypothesis, list[EvidenceItem]] | None:
    rule = next(r for r in CROSS_REPO_LINK_RULES if r.name == "shared_dependency_name")
    edges = rule.find_edges(source, other)
    if not edges:
        return None
    edge = edges[0]

    evidence: list[EvidenceItem] = []
    for index, dependency_name in enumerate(edge.properties["dependencies"]):
        evidence.append(
            EvidenceItem(
                id=f"evidence:{source.repository_id}:{other.repository_id}:dependency_match:{index}",
                kind="dependency_coordinate_match",
                source_type="metadata",
                reliability_tier=_HEURISTIC_TIER,
                reference=EvidenceReference(
                    repository_id=source.repository_id,
                    source_type="metadata",
                    locator=f"dependency:{dependency_name}",
                    key=dependency_name,
                ),
                raw_value=json.dumps(
                    {"dependency_name": dependency_name, "other_repository_name": other.name}
                ),
                provenance=provenance,
            )
        )

    hypothesis = Hypothesis(
        id=(
            "hyp:cross_repo_link_candidate:DEPENDS_ON_REPOSITORY:"
            f"{source.repository_id}:{other.repository_id}"
        ),
        relationship_type="DEPENDS_ON_REPOSITORY",
        source_entity=edge.source_id,
        target_entity=edge.target_id,
        evidence_refs=tuple(item.id for item in evidence),
        explanation=(
            f"{source.repository_id!r} declares a dependency whose name " f"matches {other.name!r}."
        ),
        provenance=provenance,
    )
    return hypothesis, evidence


class FeignTargetLiteralValidator(KnowledgeValidator):
    """Independently re-derives a `CALLS_SERVICE` confirmation by re-running
    `_identifier_match` against the raw target-name/other-repository-name
    facts in the pack — not by reading the hypothesis's own claim."""

    name = "feign_target_literal_match"
    applies_to = frozenset({"CALLS_SERVICE"})

    async def validate(
        self, hypothesis: Hypothesis, pack: EngineeringEvidencePack
    ) -> ValidationResult:
        candidates = [
            item
            for item in pack.items
            if item.id in hypothesis.evidence_refs and item.kind == "feign_client_target_match"
        ]
        confirmed = [
            item
            for item in candidates
            if _identifier_match(
                json.loads(item.raw_value)["target_name"],
                json.loads(item.raw_value)["other_repository_name"],
            )
        ]
        return self._result(hypothesis, pack, confirmed)

    def _result(
        self, hypothesis: Hypothesis, pack: EngineeringEvidencePack, confirmed: list[EvidenceItem]
    ) -> ValidationResult:
        identity = GeneratorIdentity(kind="deterministic", name=self.name, version="1.0.0")
        provenance = _provenance(pack.id, identity)
        if confirmed:
            return ValidationResult(
                hypothesis_id=hypothesis.id,
                validator_name=self.name,
                verdict="confirms",
                evidence_used=tuple(item.id for item in confirmed),
                source_type="code_annotation_literal",
                evidence_reliability_tier=_STRUCTURAL_TIER,
                explanation=(
                    "Independently re-derived via _identifier_match: the Feign "
                    "target literal matches the other repository's name."
                ),
                provenance=provenance,
            )
        return ValidationResult(
            hypothesis_id=hypothesis.id,
            validator_name=self.name,
            verdict="no_signal",
            evidence_used=(),
            source_type="code_annotation_literal",
            evidence_reliability_tier=0,
            explanation="No Feign target literal in the pack matched independently.",
            provenance=provenance,
        )


class KafkaTopicLiteralValidator(KnowledgeValidator):
    """Independently re-derives a `SHARES_TOPIC` confirmation by
    recomputing the produces/consumes topic-set intersection from atomic
    per-topic evidence — the same computation `_kafka_topic_overlap`
    performs, run again here against the raw facts, not its output."""

    name = "kafka_topic_literal_overlap"
    applies_to = frozenset({"SHARES_TOPIC"})

    async def validate(
        self, hypothesis: Hypothesis, pack: EngineeringEvidencePack
    ) -> ValidationResult:
        source_repository_id = hypothesis.source_entity.rsplit(":repository", 1)[0]
        other_repository_id = hypothesis.target_entity.rsplit(":repository", 1)[0]

        source_topics: set[str] = set()
        other_topics: set[str] = set()
        relevant: list[EvidenceItem] = []
        for item in pack.items:
            if item.id not in hypothesis.evidence_refs or item.kind != "kafka_topic_usage":
                continue
            data = json.loads(item.raw_value)
            if data["repository_id"] == source_repository_id:
                source_topics.add(data["topic"])
                relevant.append(item)
            elif data["repository_id"] == other_repository_id:
                other_topics.add(data["topic"])
                relevant.append(item)

        shared_topics = source_topics & other_topics
        identity = GeneratorIdentity(kind="deterministic", name=self.name, version="1.0.0")
        provenance = _provenance(pack.id, identity)

        if shared_topics:
            confirming_items = [
                item for item in relevant if json.loads(item.raw_value)["topic"] in shared_topics
            ]
            return ValidationResult(
                hypothesis_id=hypothesis.id,
                validator_name=self.name,
                verdict="confirms",
                evidence_used=tuple(item.id for item in confirming_items),
                source_type="code_annotation_literal",
                evidence_reliability_tier=_STRUCTURAL_TIER,
                explanation=(
                    "Independently recomputed the produces/consumes topic-set "
                    f"intersection: {sorted(shared_topics)}."
                ),
                provenance=provenance,
            )
        return ValidationResult(
            hypothesis_id=hypothesis.id,
            validator_name=self.name,
            verdict="no_signal",
            evidence_used=(),
            source_type="code_annotation_literal",
            evidence_reliability_tier=0,
            explanation="No overlapping Kafka topic found when recomputed independently.",
            provenance=provenance,
        )


class DependencyCoordinateValidator(KnowledgeValidator):
    """Independently re-derives a `DEPENDS_ON_REPOSITORY` confirmation by
    re-running `_identifier_match` against the raw dependency-coordinate
    facts in the pack."""

    name = "dependency_coordinate_name_match"
    applies_to = frozenset({"DEPENDS_ON_REPOSITORY"})

    async def validate(
        self, hypothesis: Hypothesis, pack: EngineeringEvidencePack
    ) -> ValidationResult:
        candidates = [
            item
            for item in pack.items
            if item.id in hypothesis.evidence_refs and item.kind == "dependency_coordinate_match"
        ]
        confirmed = [
            item
            for item in candidates
            if _identifier_match(
                json.loads(item.raw_value)["dependency_name"],
                json.loads(item.raw_value)["other_repository_name"],
            )
        ]
        identity = GeneratorIdentity(kind="deterministic", name=self.name, version="1.0.0")
        provenance = _provenance(pack.id, identity)
        if confirmed:
            return ValidationResult(
                hypothesis_id=hypothesis.id,
                validator_name=self.name,
                verdict="confirms",
                evidence_used=tuple(item.id for item in confirmed),
                source_type="dependency_coordinate_name",
                evidence_reliability_tier=_HEURISTIC_TIER,
                explanation=(
                    "Independently re-derived via _identifier_match: a "
                    "dependency coordinate matches the other repository's name."
                ),
                provenance=provenance,
            )
        return ValidationResult(
            hypothesis_id=hypothesis.id,
            validator_name=self.name,
            verdict="no_signal",
            evidence_used=(),
            source_type="dependency_coordinate_name",
            evidence_reliability_tier=0,
            explanation="No dependency coordinate in the pack matched independently.",
            provenance=provenance,
        )


CROSS_REPO_VALIDATORS: tuple[KnowledgeValidator, ...] = (
    FeignTargetLiteralValidator(),
    KafkaTopicLiteralValidator(),
    DependencyCoordinateValidator(),
)
