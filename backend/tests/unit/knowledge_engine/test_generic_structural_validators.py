"""RFC-07 hardening — `generic_structural.py`'s relationship-evidence
validators: endpoint existence must never, by itself, be enough to confirm
a relationship at a promotable tier. Pure unit tests (no DB, no network),
paired with `DefaultConfidenceEngine` where the point is specifically to
prove a *state*, not just a single `ValidationResult`."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from app.knowledge_engine.confidence.default_engine import DefaultConfidenceEngine
from app.knowledge_engine.contracts.confidence import ConfidenceState
from app.knowledge_engine.contracts.evidence import (
    EngineeringEvidencePack,
    EvidenceItem,
    EvidenceReference,
)
from app.knowledge_engine.contracts.hypothesis import Hypothesis
from app.knowledge_engine.contracts.provenance import GeneratorIdentity, Provenance
from app.knowledge_engine.validators.generic_structural import (
    EndpointExistenceValidator,
    GenericCallEvidenceValidator,
    GenericDependencyEvidenceValidator,
    GenericEvidenceMentionValidator,
    GenericImportEvidenceValidator,
)
from app.knowledge_engine.validators.registry import run_validators, ALL_VALIDATORS

pytestmark = pytest.mark.asyncio


def _provenance() -> Provenance:
    return Provenance(
        generator=GeneratorIdentity(kind="deterministic", name="generic_file_discovery", version="1.1.0"),
        produced_at=datetime.now(UTC),
        pack_id="pack:repo-1:abc:x",
        pack_version="v1",
        run_id="pack:repo-1:abc:x",
    )


def _node_item(node_id: str, locator: str, kind: str = "graph_node:Component:SourceFile") -> EvidenceItem:
    return EvidenceItem(
        id=f"ev:node:{node_id}",
        kind=kind,
        source_type="code",
        reliability_tier=3,
        reference=EvidenceReference(
            repository_id="repo-1", source_type="code", locator=locator, key=node_id, commit_sha="abc"
        ),
        raw_value=json.dumps({"name": locator, "file_path": locator}),
        provenance=_provenance(),
    )


def _source_text_item(node_id: str, locator: str, content: str) -> EvidenceItem:
    return EvidenceItem(
        id=f"ev:source:{node_id}",
        kind="source_file",
        source_type="code",
        reliability_tier=3,
        reference=EvidenceReference(
            repository_id="repo-1", source_type="code", locator=locator, key=node_id, commit_sha="abc"
        ),
        raw_value=content,
        provenance=_provenance(),
    )


def _llm_provenance() -> Provenance:
    # The generic-language fallback's hypotheses always carry a `kind="llm"`
    # generator identity (see `generic_language_generator._identity_for`) -
    # deliberately distinct from `_provenance()` above (used for evidence
    # items, `kind="deterministic"`, matching `generic_language_evidence
    # ._GENERATOR_IDENTITY`). Using the wrong one here would silently let
    # `deterministic_structural.py`'s deterministic-only validators treat
    # these as real parser-derived hypotheses - exactly the RFC-07
    # hardening bug this test suite exists to catch, just self-inflicted
    # via test setup instead of product code.
    return Provenance(
        generator=GeneratorIdentity(kind="llm", name="generic_language_llm:test-model", version="v2"),
        produced_at=datetime.now(UTC),
        pack_id="pack:repo-1:abc:x",
        pack_version="v1",
        run_id="pack:repo-1:abc:x",
    )


def _pack(items: list[EvidenceItem]) -> EngineeringEvidencePack:
    return EngineeringEvidencePack(
        id="pack:repo-1:abc:x", repository_id="repo-1", commit_sha="abc", schema_version="v1", items=tuple(items)
    )


def _hypothesis(
    relationship_type: str, source: str, target: str, evidence_refs: tuple[str, ...]
) -> Hypothesis:
    return Hypothesis(
        id=f"hyp:{relationship_type}:{source}:{target}",
        relationship_type=relationship_type,
        source_entity=source,
        target_entity=target,
        evidence_refs=evidence_refs,
        explanation="test",
        provenance=_llm_provenance(),
        generator_confidence=0.9,
    )


async def _confidence_state(hypothesis: Hypothesis, pack: EngineeringEvidencePack) -> ConfidenceState:
    results = await run_validators(hypothesis, pack, ALL_VALIDATORS)
    engine = DefaultConfidenceEngine()
    model = None
    for result in results:
        model = engine.aggregate(model, result)
    assert model is not None, "expected at least one applicable validator"
    return model.state


# -- Endpoint existence is weak, by design -----------------------------------


async def test_endpoint_existence_alone_is_tier_one_never_promotable() -> None:
    """RFC-07 hardening's central claim: A exists, B exists, LLM says
    A CALLS B, but nothing demonstrates an actual call - the resulting
    confidence must NOT be VERIFIED or HIGHLY_LIKELY."""
    a = _node_item("repo-1:source-file:a.go", "a.go")
    b = _node_item("repo-1:source-file:b.go", "b.go")
    a_text = _source_text_item("repo-1:source-file:a.go", "a.go", "package a\n// no mention of b here")
    pack = _pack([a, b, a_text])
    hypothesis = _hypothesis("CALLS", a.reference.key, b.reference.key, (a_text.id,))

    state = await _confidence_state(hypothesis, pack)
    assert state not in (ConfidenceState.VERIFIED, ConfidenceState.HIGHLY_LIKELY)


async def test_endpoint_existence_validator_reports_weak_tier() -> None:
    a = _node_item("repo-1:source-file:a.go", "a.go")
    b = _node_item("repo-1:source-file:b.go", "b.go")
    pack = _pack([a, b])
    hypothesis = _hypothesis("IMPORTS", a.reference.key, b.reference.key, ("ev:node:" + a.reference.key,))

    result = await EndpointExistenceValidator().validate(hypothesis, pack)
    assert result.verdict == "confirms"
    assert result.evidence_reliability_tier == 1


# -- IMPORTS -------------------------------------------------------------


async def test_explicit_import_statement_confirms_at_strong_tier() -> None:
    a = _node_item("repo-1:source-file:a.go", "a.go")
    b = _node_item("repo-1:source-file:b.go", "b.go")
    a_text = _source_text_item("repo-1:source-file:a.go", "a.go", 'package a\nimport "b"\n')
    pack = _pack([a, b, a_text])
    hypothesis = _hypothesis("IMPORTS", a.reference.key, b.reference.key, (a_text.id,))

    result = await GenericImportEvidenceValidator().validate(hypothesis, pack)
    assert result.verdict == "confirms"
    assert result.evidence_reliability_tier == 3
    assert result.source_type == "import_evidence"


async def test_import_hypothesis_with_only_endpoint_existence_reaches_verified_via_strong_signal() -> None:
    """An actual import statement, once present, does combine with
    endpoint existence to reach VERIFIED - this is the "false positive
    prevention did not neuter promotion entirely" half of the story."""
    a = _node_item("repo-1:source-file:a.go", "a.go")
    b = _node_item("repo-1:source-file:b.go", "b.go")
    a_text = _source_text_item("repo-1:source-file:a.go", "a.go", 'package a\nimport "b"\n')
    pack = _pack([a, b, a_text])
    hypothesis = _hypothesis("IMPORTS", a.reference.key, b.reference.key, (a_text.id,))

    state = await _confidence_state(hypothesis, pack)
    assert state in (ConfidenceState.VERIFIED, ConfidenceState.HIGHLY_LIKELY)


async def test_no_import_keyword_stays_no_signal() -> None:
    a = _node_item("repo-1:source-file:a.go", "a.go")
    b = _node_item("repo-1:source-file:b.go", "b.go")
    a_text = _source_text_item("repo-1:source-file:a.go", "a.go", "package a\n// mentions b in a comment")
    pack = _pack([a, b, a_text])
    hypothesis = _hypothesis("IMPORTS", a.reference.key, b.reference.key, (a_text.id,))

    result = await GenericImportEvidenceValidator().validate(hypothesis, pack)
    assert result.verdict == "no_signal"


# -- CALLS -----------------------------------------------------------------


async def test_actual_call_site_confirms_at_strong_tier() -> None:
    a = _node_item("repo-1:source-file:a.go", "a.go")
    b_symbol = _node_item(
        "repo-1:generic-symbol:b.go:process", "process", kind="graph_node:Component:GenericSymbol"
    )
    a_text = _source_text_item("repo-1:source-file:a.go", "a.go", "func a() { process() }")
    pack = _pack([a, b_symbol, a_text])
    hypothesis = _hypothesis("CALLS", a.reference.key, b_symbol.reference.key, (a_text.id,))

    result = await GenericCallEvidenceValidator().validate(hypothesis, pack)
    assert result.verdict == "confirms"
    assert result.evidence_reliability_tier == 3
    assert result.source_type == "call_site_evidence"


async def test_call_hypothesis_with_real_call_site_reaches_promotable_confidence() -> None:
    a = _node_item("repo-1:source-file:a.go", "a.go")
    b_symbol = _node_item(
        "repo-1:generic-symbol:b.go:process", "process", kind="graph_node:Component:GenericSymbol"
    )
    a_text = _source_text_item("repo-1:source-file:a.go", "a.go", "func a() { process() }")
    pack = _pack([a, b_symbol, a_text])
    hypothesis = _hypothesis("CALLS", a.reference.key, b_symbol.reference.key, (a_text.id,))

    state = await _confidence_state(hypothesis, pack)
    assert state in (ConfidenceState.VERIFIED, ConfidenceState.HIGHLY_LIKELY)


async def test_bare_name_mention_without_call_syntax_does_not_confirm() -> None:
    """`process` appearing as prose/an identifier, never followed by `(`,
    is not a call site."""
    a = _node_item("repo-1:source-file:a.go", "a.go")
    b_symbol = _node_item(
        "repo-1:generic-symbol:b.go:process", "process", kind="graph_node:Component:GenericSymbol"
    )
    a_text = _source_text_item("repo-1:source-file:a.go", "a.go", "// see process for details")
    pack = _pack([a, b_symbol, a_text])
    hypothesis = _hypothesis("CALLS", a.reference.key, b_symbol.reference.key, (a_text.id,))

    result = await GenericCallEvidenceValidator().validate(hypothesis, pack)
    assert result.verdict == "no_signal"


async def test_ambiguous_symbol_name_never_confirms() -> None:
    """Two distinct `process` symbols exist pack-wide - a bare `process(`
    call site cannot be attributed to either from text alone."""
    a = _node_item("repo-1:source-file:a.go", "a.go")
    process_1 = _node_item(
        "repo-1:generic-symbol:x.go:process", "process", kind="graph_node:Component:GenericSymbol"
    )
    process_2 = _node_item(
        "repo-1:generic-symbol:y.go:process", "process", kind="graph_node:Component:GenericSymbol"
    )
    a_text = _source_text_item("repo-1:source-file:a.go", "a.go", "func a() { process() }")
    pack = _pack([a, process_1, process_2, a_text])
    hypothesis = _hypothesis("CALLS", a.reference.key, process_1.reference.key, (a_text.id,))

    result = await GenericCallEvidenceValidator().validate(hypothesis, pack)
    assert result.verdict == "no_signal"
    assert "ambiguous" in result.explanation.lower()

    state = await _confidence_state(hypothesis, pack)
    assert state not in (ConfidenceState.VERIFIED, ConfidenceState.HIGHLY_LIKELY)


async def test_hallucinated_call_target_never_grounds_or_confirms() -> None:
    """The target symbol was never discovered at all (generator-level
    hallucination) - endpoint existence itself has nothing to confirm."""
    a = _node_item("repo-1:source-file:a.go", "a.go")
    a_text = _source_text_item("repo-1:source-file:a.go", "a.go", "func a() {}")
    pack = _pack([a, a_text])
    hypothesis = _hypothesis(
        "CALLS", a.reference.key, "repo-1:generic-symbol:nowhere.go:nonexistent_function", (a_text.id,)
    )

    result = await EndpointExistenceValidator().validate(hypothesis, pack)
    assert result.verdict == "no_signal"

    state = await _confidence_state(hypothesis, pack)
    assert state == ConfidenceState.CANDIDATE


async def test_call_site_to_a_different_unambiguous_symbol_contradicts() -> None:
    """The cited evidence shows a real call to `helper`, a specific,
    unambiguous known symbol - never to the claimed target `process` at
    all. This is genuine, evidence-backed contradiction, not mere
    absence."""
    a = _node_item("repo-1:source-file:a.go", "a.go")
    process_symbol = _node_item(
        "repo-1:generic-symbol:b.go:process", "process", kind="graph_node:Component:GenericSymbol"
    )
    helper_symbol = _node_item(
        "repo-1:generic-symbol:c.go:helper", "helper", kind="graph_node:Component:GenericSymbol"
    )
    a_text = _source_text_item("repo-1:source-file:a.go", "a.go", "func a() { helper() }")
    pack = _pack([a, process_symbol, helper_symbol, a_text])
    hypothesis = _hypothesis("CALLS", a.reference.key, process_symbol.reference.key, (a_text.id,))

    result = await GenericCallEvidenceValidator().validate(hypothesis, pack)
    assert result.verdict == "contradicts"

    state = await _confidence_state(hypothesis, pack)
    assert state in (ConfidenceState.REJECTED, ConfidenceState.CONFLICTING)


async def test_absence_of_a_call_site_alone_is_no_signal_not_contradiction() -> None:
    """No call site for the target anywhere, and no call site for any
    other unambiguous symbol either - this must stay no_signal, never
    contradicts (RFC-07 hardening requirement #10: don't interpret
    absence as contradiction)."""
    a = _node_item("repo-1:source-file:a.go", "a.go")
    process_symbol = _node_item(
        "repo-1:generic-symbol:b.go:process", "process", kind="graph_node:Component:GenericSymbol"
    )
    a_text = _source_text_item("repo-1:source-file:a.go", "a.go", "func a() { /* does nothing */ }")
    pack = _pack([a, process_symbol, a_text])
    hypothesis = _hypothesis("CALLS", a.reference.key, process_symbol.reference.key, (a_text.id,))

    result = await GenericCallEvidenceValidator().validate(hypothesis, pack)
    assert result.verdict == "no_signal"


# -- DEPENDS_ON ------------------------------------------------------------


async def test_manifest_file_mention_confirms_at_strong_tier() -> None:
    a = _node_item("repo-1:source-file:a.go", "a.go")
    dep = _node_item("repo-1:source-file:shopspring/decimal", "shopspring/decimal")
    manifest_text = _source_text_item(
        "repo-1:source-file:go.mod", "go.mod", "module x\nrequire github.com/shopspring/decimal v1.3.1\n"
    )
    pack = _pack([a, dep, manifest_text])
    hypothesis = _hypothesis("DEPENDS_ON", a.reference.key, dep.reference.key, (manifest_text.id,))

    result = await GenericDependencyEvidenceValidator().validate(hypothesis, pack)
    assert result.verdict == "confirms"
    assert result.evidence_reliability_tier == 3
    assert result.source_type == "explicit_dependency_manifest"


async def test_keyword_only_dependency_mention_confirms_at_weak_tier_and_stays_unpromoted() -> None:
    a = _node_item("repo-1:source-file:a.go", "a.go")
    dep = _node_item("repo-1:source-file:pkg.go", "pkg")
    a_text = _source_text_item("repo-1:source-file:a.go", "a.go", "// this module requires pkg heavily")
    pack = _pack([a, dep, a_text])
    hypothesis = _hypothesis("DEPENDS_ON", a.reference.key, dep.reference.key, (a_text.id,))

    result = await GenericDependencyEvidenceValidator().validate(hypothesis, pack)
    assert result.verdict == "confirms"
    assert result.evidence_reliability_tier == 1
    assert result.source_type == "dependency_keyword_heuristic"

    state = await _confidence_state(hypothesis, pack)
    assert state not in (ConfidenceState.VERIFIED, ConfidenceState.HIGHLY_LIKELY)


async def test_no_dependency_evidence_at_all_stays_no_signal() -> None:
    a = _node_item("repo-1:source-file:a.go", "a.go")
    dep = _node_item("repo-1:source-file:pkg.go", "pkg")
    a_text = _source_text_item("repo-1:source-file:a.go", "a.go", "package a // no mention of the dep")
    pack = _pack([a, dep, a_text])
    hypothesis = _hypothesis("DEPENDS_ON", a.reference.key, dep.reference.key, (a_text.id,))

    result = await GenericDependencyEvidenceValidator().validate(hypothesis, pack)
    assert result.verdict == "no_signal"


# -- GenericEvidenceMentionValidator is unchanged in spirit ----------------


async def test_evidence_mention_validator_still_never_contradicts() -> None:
    a = _node_item("repo-1:source-file:a.go", "a.go")
    b = _node_item("repo-1:source-file:b.go", "b.go")
    a_text = _source_text_item("repo-1:source-file:a.go", "a.go", "package a\nno reference here")
    pack = _pack([a, b, a_text])
    hypothesis = _hypothesis("IMPORTS", a.reference.key, b.reference.key, (a_text.id,))

    result = await GenericEvidenceMentionValidator().validate(hypothesis, pack)
    assert result.verdict == "no_signal"
