"""RFC-07 (this cycle, hardening pass) — validator coverage for the
generic-language fallback's relationship vocabulary (`IMPORTS`, `CALLS`,
`DEPENDS_ON`).

This module previously shipped with a real product-level flaw, caught
after the first successful end-to-end promotion: its only "strong"
(tier-3) signal was `A exists` + `B exists`, which is proof neither entity
was hallucinated, NOT proof the relationship itself is true. Endpoint
existence and relationship evidence are now deliberately two different
validators with two different trust levels - see `EndpointExistenceValidator`
(weak, tier 1, `source_type="endpoint_existence"`, can never alone clear
the Materializer's `{"verified", "highly_likely"}` gate) versus the three
relationship-specific evidence validators below (tier 3 only when the
evidence actually demonstrates the *specific* claim, not merely that its
endpoints are real).

Still deliberately language-agnostic - not `GoImportValidator`/
`RustCallValidator`/etc. The three relationship-evidence validators below
each cover exactly one relationship type (unlike the old single grounding
validator, which covered all three identically) because what counts as
"real evidence" genuinely differs per relationship type (RFC-07 hardening
requirement #3) - but nothing in any of them reads a language name, file
extension, or per-language syntax. Where they need a shared vocabulary at
all (import-family keywords, common manifest filenames), that vocabulary
is DATA — the same keyword list is checked identically regardless of which
language actually produced the file — not per-language code branches.

Design summary (`HYPOTHESIS_ID` -> promotion path):
- `EndpointExistenceValidator` (tier 1, `endpoint_existence`) — applies to
  all three types. Confirms iff both `source_entity`/`target_entity`
  correspond to a real `graph_node:*` evidence item. This is corroboration
  ("the LLM didn't invent these names"), never sufficient alone for
  `HIGHLY_LIKELY`/`VERIFIED` (`HIGH_RELIABILITY_TIER` is 3; this validator
  never reports higher than 1).
- `GenericEvidenceMentionValidator` (tier 1, `cited_evidence_mention`) —
  applies to all three types. Confirms iff the hypothesis's OWN cited
  evidence literally mentions the target's display name — independent
  re-derivation of the citation, not proof of the relationship's syntax.
- `GenericImportEvidenceValidator` (tier 3 when matched,
  `import_evidence`) — applies only to `IMPORTS`. Confirms iff cited
  evidence contains a generic import-family keyword
  (`_IMPORT_KEYWORDS`) co-occurring with the target's display name -
  literal textual evidence of an import-like statement, not merely that
  both files exist.
- `GenericCallEvidenceValidator` (tier 3 when matched, `call_site_evidence`;
  can `contradicts`, `call_site_mismatch`) — applies only to `CALLS`. See
  its own docstring: requires an actual call-site pattern
  (`name\\s*\\(`), refuses to confirm when the name is ambiguous
  pack-wide, and can genuinely contradict when the cited evidence shows a
  call site to a *different*, specific, unambiguous known symbol instead.
- `GenericDependencyEvidenceValidator` (tier 3 for an explicit
  manifest-file mention, `explicit_dependency_manifest`; tier 1 for a bare
  keyword co-occurrence, `dependency_keyword_heuristic`) — applies only to
  `DEPENDS_ON`. See its own docstring for the tiering rationale.

None of these ever promote a hypothesis by themselves through some
separate scoring system - every `ValidationResult` they produce flows
through the exact same `DefaultConfidenceEngine`/`ALL_VALIDATORS` pipeline
every other validator in this codebase already uses. "Two distinct
confirming source types, at least one at tier >= 3" (the engine's existing
VERIFIED rule) now genuinely means "endpoint existence AND direct
relationship evidence agree," or "two independent relationship-evidence
signals agree" - not "both of two equally-weak existence checks agree with
themselves," which is what the pre-hardening design allowed.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from app.indexer.hypotheses.deterministic_generator import node_evidence_items_by_node_id
from app.knowledge_engine.contracts.evidence import EngineeringEvidencePack, EvidenceItem
from app.knowledge_engine.contracts.hypothesis import Hypothesis
from app.knowledge_engine.contracts.provenance import GeneratorIdentity, Provenance
from app.knowledge_engine.contracts.validation import KnowledgeValidator, ValidationResult

_STRONG_TIER = 3
_WEAK_TIER = 1

# The generic-language fallback's own relationship vocabulary (see
# `generic_language_generator._ALLOWED_RELATIONSHIP_TYPES`) - reused
# verbatim, not redeclared, so the two can never drift apart.
GENERIC_RELATIONSHIP_TYPES = frozenset({"IMPORTS", "CALLS", "DEPENDS_ON"})

# Shared, language-agnostic vocabulary - data, not per-language code. Each
# keyword is common to at least one mainstream language's import/include
# syntax (Python `import`/`from`, Go `import`, Rust `use`, JS/TS
# `import`/`require`, C/C++ `#include`, C# `using`, Ruby/PHP `require`,
# Java `import`) - checked identically regardless of which language
# actually produced the file, exactly the same way `_DECLARATION_PATTERN`
# in `generic_language_evidence.py` already checks one shared
# function-declaration shape across languages.
_IMPORT_KEYWORDS = ("import", "require", "include", "using", "use", "from")
_DEPENDENCY_KEYWORDS = ("depends", "require", "requires", "dependency", "uses", "needs")

# Well-known dependency-manifest filenames across mainstream ecosystems -
# again data, not per-language code: recognizing a filename is not the
# same thing as parsing that ecosystem's manifest syntax. A cited-evidence
# item whose own file happens to be one of these carries a materially
# stronger "this is a real declared dependency" prior than an arbitrary
# source file mentioning a name in passing.
_MANIFEST_BASENAMES = frozenset(
    {
        "go.mod",
        "package.json",
        "requirements.txt",
        "pyproject.toml",
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "gemfile",
        "cargo.toml",
        "composer.json",
        "pipfile",
    }
)


def _provenance(validator_name: str) -> Provenance:
    return Provenance(
        generator=GeneratorIdentity(kind="rule", name=validator_name, version="2.0.0"),
        produced_at=datetime.now(UTC),
        pack_id="n/a-validator",
        pack_version="v1",
        run_id="n/a-validator",
    )


def _entity_display_name(item: EvidenceItem) -> str | None:
    """The name a hypothesis's cited evidence text would plausibly mention
    for this entity - its `reference.locator` (the file path, for a
    SourceFile/GenericSymbol node) with any directory prefix and file
    extension stripped, so `pipeline/orders/orders.go` -> `orders` and a
    bare function name like `Summarize` stays as-is. Deliberately the same
    "basename, no extension" normalization for every entity kind - no
    per-language-aware name extraction."""
    locator = item.reference.locator
    if not locator:
        return None
    basename = locator.rsplit("/", 1)[-1]
    if "." in basename:
        basename = basename.rsplit(".", 1)[0]
    return basename or None


def _cited_items(hypothesis: Hypothesis, pack: EngineeringEvidencePack) -> dict[str, EvidenceItem]:
    return {item.id: item for item in pack.items if item.id in hypothesis.evidence_refs}


def _no_signal(
    validator_name: str, hypothesis: Hypothesis, source_type: str, reason: str
) -> ValidationResult:
    return ValidationResult(
        hypothesis_id=hypothesis.id,
        validator_name=validator_name,
        verdict="no_signal",
        evidence_used=(),
        source_type=source_type,
        evidence_reliability_tier=0,
        explanation=reason,
        provenance=_provenance(validator_name),
    )


def _has_keyword(text: str, keywords: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(re.search(rf"\b{re.escape(keyword)}\b", lowered) for keyword in keywords)


class EndpointExistenceValidator(KnowledgeValidator):
    """Endpoint existence, and nothing more - deliberately renamed from
    (and weakened relative to) this module's original
    `GenericRelationshipGroundingValidator`. `A exists` and `B exists` is
    real, but it is corroboration that the hypothesis isn't a hallucinated
    reference, NOT evidence that the relationship it claims between them
    is true - RFC-07's hardening pass exists specifically because the
    original design conflated the two. Tier 1 (`_WEAK_TIER`): can never by
    itself put a hypothesis's `max_confirming_reliability_tier` at or
    above `HIGH_RELIABILITY_TIER` (3), so it can never alone reach
    `HIGHLY_LIKELY`/`VERIFIED` - see `DefaultConfidenceEngine._state_for`.
    """

    name = "generic_endpoint_existence"
    applies_to = GENERIC_RELATIONSHIP_TYPES

    async def validate(
        self, hypothesis: Hypothesis, pack: EngineeringEvidencePack
    ) -> ValidationResult:
        nodes = node_evidence_items_by_node_id(pack)
        source_item = nodes.get(hypothesis.source_entity)
        target_item = nodes.get(hypothesis.target_entity)

        if source_item is None or target_item is None:
            return _no_signal(
                self.name,
                hypothesis,
                "endpoint_existence",
                "source or target entity has no corresponding graph_node evidence "
                "in this pack - cannot confirm even endpoint existence",
            )

        return ValidationResult(
            hypothesis_id=hypothesis.id,
            validator_name=self.name,
            verdict="confirms",
            evidence_used=(source_item.id, target_item.id),
            source_type="endpoint_existence",
            evidence_reliability_tier=_WEAK_TIER,
            explanation=(
                f"both {hypothesis.source_entity!r} and {hypothesis.target_entity!r} "
                "correspond to a real, discovered graph node - this confirms neither "
                "endpoint was hallucinated, not that the relationship itself is true"
            ),
            provenance=_provenance(self.name),
        )


class GenericEvidenceMentionValidator(KnowledgeValidator):
    """Does the hypothesis's OWN cited evidence (never evidence it didn't
    cite - same discipline every validator in this codebase already
    follows) literally contain the target entity's name/basename as a
    substring? Independent re-derivation, not circularity: the generator
    citing an evidence id doesn't by itself prove that evidence supports
    the claim - a generator could cite irrelevant evidence, and this
    validator is what actually checks the citation is real. Heuristic
    (tier 1), not deterministic, because substring matching against free
    text can have false positives (a name appearing in an unrelated
    comment) - the same conservative tier `evidence_keyword.py`'s own
    keyword matching already uses, for exactly the same reason. Only ever
    `confirms`/`no_signal`, never `contradicts` - absence of a mention is
    not proof the relationship is false, the same discipline
    `evidence_keyword.py` documents for its own keyword absence case."""

    name = "generic_evidence_mention"
    applies_to = GENERIC_RELATIONSHIP_TYPES

    async def validate(
        self, hypothesis: Hypothesis, pack: EngineeringEvidencePack
    ) -> ValidationResult:
        nodes = node_evidence_items_by_node_id(pack)
        target_item = nodes.get(hypothesis.target_entity)
        target_name = _entity_display_name(target_item) if target_item is not None else None

        if not target_name:
            return _no_signal(
                self.name, hypothesis, "cited_evidence_mention", "target entity has no derivable display name"
            )

        for item in _cited_items(hypothesis, pack).values():
            if target_name.lower() in item.raw_value.lower():
                return ValidationResult(
                    hypothesis_id=hypothesis.id,
                    validator_name=self.name,
                    verdict="confirms",
                    evidence_used=(item.id,),
                    source_type="cited_evidence_mention",
                    evidence_reliability_tier=_WEAK_TIER,
                    explanation=(
                        f"hypothesis's own cited evidence {item.id!r} literally mentions "
                        f"{target_name!r}"
                    ),
                    provenance=_provenance(self.name),
                )
        return _no_signal(
            self.name,
            hypothesis,
            "cited_evidence_mention",
            f"none of the hypothesis's cited evidence mentions {target_name!r}",
        )


class GenericImportEvidenceValidator(KnowledgeValidator):
    """`IMPORTS` requires evidence an import-like statement actually
    exists, not merely that both files exist (RFC-07 hardening
    requirement #3/#7). Confirms at `_STRONG_TIER` iff the hypothesis's
    own cited evidence contains BOTH a generic import-family keyword
    (`_IMPORT_KEYWORDS`) AND the target's display name - literal
    co-occurrence, still no per-language import-grammar parsing. A real
    import statement (`import "example.com/widgets/orders"`,
    `from orders import Summarize`, `use orders::Summarize`, `#include
    "orders.h"`) satisfies this; two files that both happen to exist, with
    no such statement anywhere in the cited evidence, do not - `no_signal`,
    not a fabricated confirmation. Never `contradicts`: absence of an
    import keyword doesn't prove the two files aren't related some other
    way (e.g. a transitive/generated import) - only that this validator
    found nothing to confirm on."""

    name = "generic_import_evidence"
    applies_to = frozenset({"IMPORTS"})

    async def validate(
        self, hypothesis: Hypothesis, pack: EngineeringEvidencePack
    ) -> ValidationResult:
        nodes = node_evidence_items_by_node_id(pack)
        target_item = nodes.get(hypothesis.target_entity)
        target_name = _entity_display_name(target_item) if target_item is not None else None
        if not target_name:
            return _no_signal(
                self.name, hypothesis, "import_evidence", "target entity has no derivable display name"
            )

        for item in _cited_items(hypothesis, pack).values():
            if target_name.lower() in item.raw_value.lower() and _has_keyword(
                item.raw_value, _IMPORT_KEYWORDS
            ):
                return ValidationResult(
                    hypothesis_id=hypothesis.id,
                    validator_name=self.name,
                    verdict="confirms",
                    evidence_used=(item.id,),
                    source_type="import_evidence",
                    evidence_reliability_tier=_STRONG_TIER,
                    explanation=(
                        f"cited evidence {item.id!r} contains an import-family keyword "
                        f"together with {target_name!r} - literal evidence of an import "
                        "statement, not merely that both files exist"
                    ),
                    provenance=_provenance(self.name),
                )
        return _no_signal(
            self.name,
            hypothesis,
            "import_evidence",
            "no cited evidence contains an import-family keyword co-occurring with "
            f"{target_name!r}",
        )


class GenericCallEvidenceValidator(KnowledgeValidator):
    """`CALLS` requires the strongest bar of the three (RFC-07 hardening
    requirement #6, "the most important test") - endpoint existence is
    explicitly NOT enough. Confirms at `_STRONG_TIER` only when cited
    evidence contains an actual call-site pattern for the target's name
    (`name\\s*\\(` - an invocation, not merely the name appearing as
    prose or an identifier elsewhere) AND that name is unambiguous
    pack-wide.

    Ambiguity handling (requirement #13 test 5): if more than one
    discovered entity in the pack shares the target's display name (e.g.
    two different `process` functions in two different files), a bare
    `process(` call site cannot be attributed to one over the other from
    text alone - this validator deliberately refuses to confirm in that
    case (`no_signal`, explained as ambiguous), staying inside this
    codebase's existing three-verdict vocabulary rather than inventing a
    fourth "ambiguous" verdict (RFC-07 hardening requirement #1: no
    separate trust system) - a hypothesis that only ever gets `no_signal`
    here still correctly caps out below the promotion gate.

    Contradiction (requirement #10/#13 test 6): if the cited evidence
    contains a call site for a DIFFERENT, specific, unambiguous known
    symbol, and no call site for the claimed target at all, this is
    treated as `contradicts`, not merely absence - the cited evidence
    affirmatively demonstrates a call to something else. This is
    conservative by construction: it only fires when there is a genuine,
    unambiguous alternative call site to point at, never merely because
    the target's own call site wasn't found (that alone stays
    `no_signal`, per `_state_for`'s reject-only-on-real-evidence rule)."""

    name = "generic_call_evidence"
    applies_to = frozenset({"CALLS"})

    async def validate(
        self, hypothesis: Hypothesis, pack: EngineeringEvidencePack
    ) -> ValidationResult:
        nodes = node_evidence_items_by_node_id(pack)
        target_item = nodes.get(hypothesis.target_entity)
        target_name = _entity_display_name(target_item) if target_item is not None else None
        if not target_name:
            return _no_signal(
                self.name, hypothesis, "call_site_evidence", "target entity has no derivable display name"
            )

        name_counts = self._display_name_counts(nodes)
        target_is_ambiguous = name_counts.get(target_name.lower(), 0) > 1

        cited = _cited_items(hypothesis, pack)
        target_pattern = re.compile(rf"\b{re.escape(target_name)}\s*\(")

        if not target_is_ambiguous:
            for item in cited.values():
                if target_pattern.search(item.raw_value):
                    return ValidationResult(
                        hypothesis_id=hypothesis.id,
                        validator_name=self.name,
                        verdict="confirms",
                        evidence_used=(item.id,),
                        source_type="call_site_evidence",
                        evidence_reliability_tier=_STRONG_TIER,
                        explanation=(
                            f"cited evidence {item.id!r} contains an actual call-site "
                            f"pattern for {target_name!r} ({target_name}(...)), not merely "
                            "the endpoint's existence"
                        ),
                        provenance=_provenance(self.name),
                    )
        else:
            for item in cited.values():
                if target_pattern.search(item.raw_value):
                    return _no_signal(
                        self.name,
                        hypothesis,
                        "call_site_evidence",
                        f"{target_name!r} is ambiguous - {name_counts[target_name.lower()]} "
                        "distinct discovered entities share this name pack-wide, so a bare "
                        "call site cannot be attributed to this specific target from text alone",
                    )

        # No call site for the target - check for an affirmative call site
        # to a DIFFERENT, unambiguous known symbol before giving up with
        # no_signal; that specific case is genuine contradiction, not mere
        # absence.
        mismatch = self._find_unambiguous_mismatch(
            cited, nodes, name_counts, target_name, hypothesis.source_entity
        )
        if mismatch is not None:
            item, other_name = mismatch
            return ValidationResult(
                hypothesis_id=hypothesis.id,
                validator_name=self.name,
                verdict="contradicts",
                evidence_used=(item.id,),
                source_type="call_site_evidence",
                evidence_reliability_tier=_STRONG_TIER,
                explanation=(
                    f"cited evidence {item.id!r} contains a call site for "
                    f"{other_name!r}, a different, unambiguous known entity - not for "
                    f"the claimed target {target_name!r}"
                ),
                provenance=_provenance(self.name),
            )

        return _no_signal(
            self.name,
            hypothesis,
            "call_site_evidence",
            f"no cited evidence contains a call-site pattern for {target_name!r}",
        )

    @staticmethod
    def _display_name_counts(nodes: dict[str, EvidenceItem]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in nodes.values():
            display_name = _entity_display_name(item)
            if display_name:
                counts[display_name.lower()] = counts.get(display_name.lower(), 0) + 1
        return counts

    @staticmethod
    def _find_unambiguous_mismatch(
        cited: dict[str, EvidenceItem],
        nodes: dict[str, EvidenceItem],
        name_counts: dict[str, int],
        target_name: str,
        source_entity: str,
    ) -> tuple[EvidenceItem, str] | None:
        # Only ever consider OTHER callable symbols (`GenericSymbol`
        # nodes), never whole files - a source file's own name showing up
        # in its own declaration line (`func a() {...}`) is not a call
        # site to anything, and treating a file's own display name as a
        # "different symbol" produced a false contradiction (a source file
        # calling nothing still "matched" its own declaration syntax).
        # The hypothesis's own source entity is excluded for the same
        # self-reference reason.
        candidate_names = {
            name
            for node_id, name in (
                (node_id, _entity_display_name(item))
                for node_id, item in nodes.items()
                if item.kind == "graph_node:Component:GenericSymbol" and node_id != source_entity
            )
            if name and name.lower() != target_name.lower() and name_counts.get(name.lower(), 0) == 1
        }
        for item in cited.values():
            for other_name in candidate_names:
                if re.search(rf"\b{re.escape(other_name)}\s*\(", item.raw_value):
                    return item, other_name
        return None


class GenericDependencyEvidenceValidator(KnowledgeValidator):
    """`DEPENDS_ON` is inherently more semantic than `IMPORTS`/`CALLS`
    (RFC-07 hardening requirement #8), so this validator uses a genuine
    two-tier model rather than one flat threshold:

    - `_STRONG_TIER` (`explicit_dependency_manifest`): the cited evidence
      item's OWN file is one of a well-known dependency-manifest filenames
      (`_MANIFEST_BASENAMES` - `go.mod`, `package.json`,
      `requirements.txt`, ... - data, not per-language parsing) AND its
      content mentions the target's display name - the closest this
      generic pipeline can get to "explicit configuration/manifest
      evidence" without writing a per-ecosystem manifest parser.
    - `_WEAK_TIER` (`dependency_keyword_heuristic`): a generic
      dependency-family keyword (`_DEPENDENCY_KEYWORDS`) co-occurs with
      the target's name in cited evidence that is NOT a recognized
      manifest file - real signal, but not strong enough alone to promote
      (matches requirement #8's "LLM + weak corroboration -> still not
      Verified/Highly Likely" tiering, achieved by staying under
      `HIGH_RELIABILITY_TIER` rather than by a second scoring system).
    - Neither present: `no_signal` - "both endpoints exist" is never, by
      itself, treated as dependency evidence here (that signal is
      `EndpointExistenceValidator`'s job, at its own weak tier)."""

    name = "generic_dependency_evidence"
    applies_to = frozenset({"DEPENDS_ON"})

    async def validate(
        self, hypothesis: Hypothesis, pack: EngineeringEvidencePack
    ) -> ValidationResult:
        nodes = node_evidence_items_by_node_id(pack)
        target_item = nodes.get(hypothesis.target_entity)
        target_name = _entity_display_name(target_item) if target_item is not None else None
        if not target_name:
            return _no_signal(
                self.name, hypothesis, "dependency_evidence", "target entity has no derivable display name"
            )

        cited = _cited_items(hypothesis, pack)
        weak_match: EvidenceItem | None = None
        for item in cited.values():
            if target_name.lower() not in item.raw_value.lower():
                continue
            if self._is_manifest_file(item):
                return ValidationResult(
                    hypothesis_id=hypothesis.id,
                    validator_name=self.name,
                    verdict="confirms",
                    evidence_used=(item.id,),
                    source_type="explicit_dependency_manifest",
                    evidence_reliability_tier=_STRONG_TIER,
                    explanation=(
                        f"cited evidence {item.id!r} is a recognized dependency-manifest "
                        f"file and mentions {target_name!r} - explicit declared-dependency "
                        "evidence, not merely that both endpoints exist"
                    ),
                    provenance=_provenance(self.name),
                )
            if weak_match is None and _has_keyword(item.raw_value, _DEPENDENCY_KEYWORDS):
                weak_match = item

        if weak_match is not None:
            return ValidationResult(
                hypothesis_id=hypothesis.id,
                validator_name=self.name,
                verdict="confirms",
                evidence_used=(weak_match.id,),
                source_type="dependency_keyword_heuristic",
                evidence_reliability_tier=_WEAK_TIER,
                explanation=(
                    f"cited evidence {weak_match.id!r} contains a dependency-family "
                    f"keyword together with {target_name!r}, but is not a recognized "
                    "manifest file - heuristic, not explicit, dependency evidence"
                ),
                provenance=_provenance(self.name),
            )

        return _no_signal(
            self.name,
            hypothesis,
            "dependency_evidence",
            f"no cited evidence provides manifest or keyword-level dependency evidence for "
            f"{target_name!r}",
        )

    @staticmethod
    def _is_manifest_file(item: EvidenceItem) -> bool:
        locator = item.reference.locator or ""
        basename = locator.rsplit("/", 1)[-1].lower()
        return basename in _MANIFEST_BASENAMES


GENERIC_STRUCTURAL_VALIDATORS: tuple[KnowledgeValidator, ...] = (
    EndpointExistenceValidator(),
    GenericEvidenceMentionValidator(),
    GenericImportEvidenceValidator(),
    GenericCallEvidenceValidator(),
    GenericDependencyEvidenceValidator(),
)
