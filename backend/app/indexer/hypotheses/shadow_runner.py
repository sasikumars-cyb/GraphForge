"""ADR 0018 RFC-02B / RFC-04 — runs the full shadow reasoning pipeline
(generate -> validate -> aggregate confidence -> persist) during real
indexing runs: `HypothesisGenerator -> KnowledgeValidator ->
ConfidenceEngine -> Engineering Memory -> Stop`, exactly the pipeline
RFC-04 specifies. Still shadow mode throughout — no graph writes, no
projection into Neo4j, and (as of RFC-04) persistence is itself optional
and additive: see the `db` parameter below.

Scope, deliberately narrow, unchanged since RFC-02B: this module's job is
to execute the pipeline and log what happened; it does not write to any
graph repository, and it never projects anything into Neo4j.

`db: AsyncSession | None = None` (added in RFC-04): `index_repository`
was deliberately designed (ADR 0007) to be callable and testable without
a database — see its own docstring, "takes plain values rather than ORM
objects specifically so it's testable without a database at all." Rather
than breaking that invariant, persistence here is opt-in: when `db` is
`None` (every call site and every test that predates RFC-04), this
function behaves exactly as it did before — generation, validation, and
confidence aggregation run and get logged, nothing is persisted. Only
`run_indexing` (which already holds a session) passes one, and only then
does Engineering Memory persistence happen. This preserves `index_repository
`'s DB-independence for every existing caller rather than threading a
required session through a function explicitly designed not to need one.

`commit_sha`: `index_repository` clones by `ref` (a branch/tag name) and
never resolves or exposes a concrete commit SHA today. Using `ref` as the
evidence pack's `commit_sha` remains a deliberate stand-in (RFC-02B) —
now genuinely persisted (RFC-04), but still not depended on for anything
that needs a real commit SHA's precision. Resolving one for real would be
a behavior change to `repository_cloner.py`, out of scope for this RFC
same as it was for RFC-02B.

`GENERATOR_REGISTRY` (added for the Frontier Hypothesis Generator, ADR
0018): the deterministic parser generator itself stays directly
instantiated below, unchanged — its identity depends on the parsed
`ArchitectureModel`'s language/framework, known only here, so it isn't a
registry entry. `build_generator_registry()` (generator_registry.py) is
for every OTHER generator, whose identity is fixed ahead of time; each one
runs isolated from the others and from the deterministic generator — one
generator's failure (a timeout, an unreachable LLM provider) never affects
another's hypotheses or persistence, the isolation ADR 0018's RFC-02B
roadmap entry named as the trigger condition for this evolution.
"""

from __future__ import annotations

import dataclasses
import logging
import time
import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.indexer.hypotheses.deterministic_generator import (
    DeterministicParserHypothesisGenerator,
    architecture_model_to_evidence_pack,
)
from app.indexer.hypotheses.generator_registry import build_generator_registry
from app.indexer.hypotheses.repository_evidence import (
    RepositoryEvidenceFact,
    repository_evidence_facts_to_items,
)
from app.indexer.models.architecture import ArchitectureModel
from app.knowledge_engine.confidence.default_engine import (
    FORMULA_VERSION as CONFIDENCE_FORMULA_VERSION,
)
from app.knowledge_engine.confidence.default_engine import DefaultConfidenceEngine
from app.knowledge_engine.contracts.confidence import ConfidenceModel, ConfidenceState
from app.knowledge_engine.contracts.evidence import EngineeringEvidencePack
from app.knowledge_engine.contracts.explanation import ConfidenceExplanation
from app.knowledge_engine.contracts.generator_policy import GeneratorExecutionContext
from app.knowledge_engine.contracts.hypothesis import Hypothesis
from app.knowledge_engine.contracts.knowledge import KnowledgeRelationship
from app.knowledge_engine.contracts.provenance import GeneratorIdentity
from app.knowledge_engine.contracts.validation import KnowledgeValidator
from app.knowledge_engine.explainability import explain_confidence
from app.knowledge_engine.memory_service import EngineeringMemoryService
from app.knowledge_engine.validators.registry import ALL_VALIDATORS, run_validators

logger = logging.getLogger(__name__)


def _generator_identity(model: ArchitectureModel) -> GeneratorIdentity:
    name = (
        f"{model.language}_parser"
        if not model.framework
        else f"{model.language}_{model.framework}_parser"
    )
    return GeneratorIdentity(kind="deterministic", name=name, version="1.0.0")


async def to_knowledge_relationship(
    hypothesis: Hypothesis,
    pack: EngineeringEvidencePack,
    engine: DefaultConfidenceEngine,
    validators: tuple[KnowledgeValidator, ...] = ALL_VALIDATORS,
) -> tuple[KnowledgeRelationship, ConfidenceModel, ConfidenceExplanation, int, int]:
    """One hypothesis, fully reasoned through: validate -> aggregate ->
    explain. Returns the resulting `KnowledgeRelationship`, its
    `ConfidenceModel`, a `ConfidenceExplanation` derived from the exact
    `ValidationResult`s that produced it (ADR 0018 Confidence
    Explainability — computed here, not re-derived later, because
    `ValidationResult`s are never persisted; this is the only point in the
    pipeline that ever holds them), plus counts (confirms, contradicts)
    for logging. Pure reasoning — no I/O beyond the pack already in
    memory, no database.

    `validators` defaults to `ALL_VALIDATORS` (every registered validator
    family — deterministic structural, cross-repository, evidence-keyword)
    but is a parameter, not hardcoded, specifically so other callers can
    reason through hypotheses with the same validate-then-aggregate
    mechanics against a narrower, explicit set without duplicating this
    function — see `cross_repo_memory.py`, which passes
    `CROSS_REPO_VALIDATORS` explicitly rather than relying on the default.
    Safe to default to the full aggregate here: `run_validators` only ever
    dispatches a validator to a hypothesis whose `relationship_type` is in
    that validator's own `applies_to`, so every existing caller of this
    function sees exactly the same validators fire as before — only the
    Frontier generator's previously-unvalidated capability hypotheses gain
    new coverage.
    """
    results = await run_validators(hypothesis, pack, validators)
    confidence: ConfidenceModel | None = None
    confirms = 0
    contradicts = 0
    for result in results:
        if result.verdict == "confirms":
            confirms += 1
        elif result.verdict == "contradicts":
            contradicts += 1
        confidence = engine.aggregate(confidence, result)

    if confidence is None:
        # No applicable validator produced any result at all — CANDIDATE,
        # matching DefaultConfidenceEngine's own "no confirmations, no
        # contradictions" case. Not expected in practice for the
        # deterministic generator's relationship types (RFC-03A proved
        # 100% coverage), but a generic fallback rather than an assumption
        # for whatever a future generator (RFC-06+) might produce.
        confidence = ConfidenceModel(
            state=ConfidenceState.CANDIDATE,
            distinct_confirming_source_types=0,
            confirming_source_types=frozenset(),
            max_confirming_reliability_tier=0,
            contradiction_count=0,
            computed_at=datetime.now(UTC),
            formula_version=CONFIDENCE_FORMULA_VERSION,
        )

    relationship = KnowledgeRelationship(
        id=(
            f"rel:{hypothesis.source_entity}:{hypothesis.relationship_type}:"
            f"{hypothesis.target_entity}"
        ),
        relationship_type=hypothesis.relationship_type,
        source_entity=hypothesis.source_entity,
        target_entity=hypothesis.target_entity,
        confidence=confidence,
        hypothesis_ids=(hypothesis.id,),
        provenance=(hypothesis.provenance,),
    )
    explanation = explain_confidence(confidence, results)
    return relationship, confidence, explanation, confirms, contradicts


async def _reason_hypotheses(
    hypotheses: list[Hypothesis],
    pack: EngineeringEvidencePack,
    engine: DefaultConfidenceEngine,
    *,
    repository_id: str,
    generator_name: str,
) -> tuple[list[KnowledgeRelationship], list[ConfidenceExplanation], int, int]:
    """Validate -> aggregate -> explain every hypothesis one generator
    produced, with per-hypothesis failure isolation — one malformed
    hypothesis must not discard every other one this same generator
    produced. `explanations` is positionally aligned with `relationships`
    (ADR 0018 Confidence Explainability) — passed straight through to
    `EngineeringMemoryService.store_relationships` unchanged."""
    relationships: list[KnowledgeRelationship] = []
    explanations: list[ConfidenceExplanation] = []
    confirms_total = 0
    contradicts_total = 0
    for hypothesis in hypotheses:
        try:
            relationship, _confidence, explanation, confirms, contradicts = (
                await to_knowledge_relationship(hypothesis, pack, engine)
            )
        except Exception:
            logger.exception(
                "shadow_hypothesis_reasoning_failed generator=%s hypothesis_id=%s "
                "repository_id=%s",
                generator_name,
                hypothesis.id,
                repository_id,
            )
            continue
        relationships.append(relationship)
        explanations.append(explanation)
        confirms_total += confirms
        contradicts_total += contradicts
    return relationships, explanations, confirms_total, contradicts_total


async def run_shadow_hypothesis_generation(
    *,
    repository_id: str,
    commit_sha: str,
    model: ArchitectureModel,
    db: AsyncSession | None = None,
    repository_evidence_facts: list[RepositoryEvidenceFact] | None = None,
) -> None:
    """Best-effort only. Never raises — a failure here must never fail
    repository indexing (see this module's docstring). The caller is
    expected to invoke this after the real graph has already been built
    and persisted, so even a hang or crash here cannot affect what was
    already committed to the graph repository.

    `repository_evidence_facts` (Frontier Hypothesis Generator Finding 1):
    README/manifest/config/metadata facts gathered by `index_repository`
    while the clone was still alive, merged into the same pack every
    generator reads — additive only, the deterministic generator's own
    node/edge evidence is unaffected, it simply ignores evidence `kind`s
    it doesn't recognize.
    """
    parser_identity = _generator_identity(model)
    started_at = time.monotonic()
    try:
        pack = architecture_model_to_evidence_pack(
            repository_id=repository_id,
            commit_sha=commit_sha,
            model=model,
            identity=parser_identity,
        )
        if repository_evidence_facts:
            extra_items = repository_evidence_facts_to_items(
                repository_evidence_facts,
                repository_id=repository_id,
                commit_sha=commit_sha,
                pack_id=pack.id,
            )
            pack = dataclasses.replace(pack, items=pack.items + tuple(extra_items))
    except Exception:
        elapsed_seconds = time.monotonic() - started_at
        logger.exception(
            "shadow_hypothesis_generation_failed repository_id=%s elapsed_seconds=%.3f",
            repository_id,
            elapsed_seconds,
        )
        return

    engine = DefaultConfidenceEngine()
    all_relationships: list[KnowledgeRelationship] = []
    all_explanations: list[ConfidenceExplanation] = []
    total_hypotheses = 0
    total_confirms = 0
    total_contradicts = 0
    generators_run: list[str] = []

    # The deterministic parser generator: always runs, never registry-
    # routed (see this module's own docstring and generator_registry.py's
    # for why), but isolated from every other generator exactly the same
    # way they're isolated from each other and from it.
    try:
        deterministic_generator = DeterministicParserHypothesisGenerator(parser_identity)
        hypotheses = await deterministic_generator.generate(pack)
        total_hypotheses += len(hypotheses)
        generators_run.append(parser_identity.name)
        relationships, explanations, confirms, contradicts = await _reason_hypotheses(
            hypotheses,
            pack,
            engine,
            repository_id=repository_id,
            generator_name=parser_identity.name,
        )
        all_relationships.extend(relationships)
        all_explanations.extend(explanations)
        total_confirms += confirms
        total_contradicts += contradicts
    except Exception:
        logger.exception(
            "shadow_generator_failed generator=%s repository_id=%s",
            parser_identity.name,
            repository_id,
        )

    for registered in build_generator_registry():
        context = GeneratorExecutionContext(
            repository_id=repository_id,
            commit_sha=commit_sha,
            generator_identity=registered.identity,
        )
        try:
            if not await registered.policy.should_run(context):
                continue
            generator = registered.factory()
            hypotheses = await generator.generate(pack)
        except Exception:
            logger.exception(
                "shadow_generator_failed generator=%s repository_id=%s",
                registered.identity.name,
                repository_id,
            )
            continue

        total_hypotheses += len(hypotheses)
        generators_run.append(registered.identity.name)
        relationships, explanations, confirms, contradicts = await _reason_hypotheses(
            hypotheses,
            pack,
            engine,
            repository_id=repository_id,
            generator_name=registered.identity.name,
        )
        all_relationships.extend(relationships)
        all_explanations.extend(explanations)
        total_confirms += confirms
        total_contradicts += contradicts

    persisted_evidence = False
    persisted_relationship_count = 0
    if db is not None:
        try:
            memory = EngineeringMemoryService(db)
            await memory.store_evidence_pack(uuid.UUID(repository_id), pack)
            persisted_evidence = True
            if all_relationships:
                await memory.store_relationships(
                    uuid.UUID(repository_id), all_relationships, all_explanations
                )
            persisted_relationship_count = len(all_relationships)
        except Exception:
            logger.exception("shadow_persistence_failed repository_id=%s", repository_id)

    elapsed_seconds = time.monotonic() - started_at
    logger.info(
        "shadow_hypothesis_generation_succeeded repository_id=%s generators=%s "
        "evidence_count=%d hypothesis_count=%d validated_relationship_count=%d "
        "confirmations=%d contradictions=%d persisted_evidence=%s "
        "persisted_relationship_count=%d elapsed_seconds=%.3f",
        repository_id,
        ",".join(generators_run),
        len(pack.items),
        total_hypotheses,
        len(all_relationships),
        total_confirms,
        total_contradicts,
        persisted_evidence,
        persisted_relationship_count,
        elapsed_seconds,
    )
