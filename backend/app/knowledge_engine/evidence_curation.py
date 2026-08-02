"""Bounds an `EngineeringEvidencePack` down to a prompt-sized, deterministic
subset. Deliberately NOT a reuse of
`app.context_pipeline.reasoning.curation.curate()`: that function ranks
components against a ticket's own text via a hop-bounded neighborhood
traversal seeded from request-matching anchors — none of which exist here
(there is no ticket, no request, no seeded neighborhood; just one
repository's own evidence, with no external text to rank it against). The
budgeted-with-honest-excluded-count *discipline* is intentionally the same
pattern (`EvidencePackage.excluded_count`'s reasoning, restated for a
different scoring problem: kind-diversity sampling, not relevance
ranking), not the code.

Generic, not LLM-specific: any future generator facing "the pack can be
large, the consumer's budget is not" reuses this rather than each writing
its own truncation.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from app.knowledge_engine.contracts.evidence import EngineeringEvidencePack, EvidenceItem

# Repository-level evidence (README, manifests, metadata, config) is
# always included, uncapped by kind: there are only ever a handful of
# these per repository (see repository_evidence.py's own small,
# allowlist-bounded extraction), and they're the most information-dense
# evidence available for repository-level hypotheses — unlike
# `graph_node:*`/`graph_edge:*`, which can run into the thousands for a
# large repository.
_ALWAYS_INCLUDE_KIND_PREFIX = "repository_"

_PER_KIND_BUDGET = 5
_MAX_SAMPLED_ITEMS = 40


@dataclass(frozen=True)
class CuratedEvidence:
    items: tuple[EvidenceItem, ...]
    excluded_count: int
    total_candidates: int


def curate_for_prompt(pack: EngineeringEvidencePack) -> CuratedEvidence:
    """Every `repository_*` item, plus up to `_PER_KIND_BUDGET` items per
    remaining evidence `kind` (first-seen, in pack order — pack order is
    itself deterministic, see `deterministic_generator.py`), capped overall
    at `_MAX_SAMPLED_ITEMS` — a representative sample of what KINDS of
    facts exist, not an attempt to rank which individual items matter
    most (there's no ticket/request here to rank against)."""
    always_include = [
        item for item in pack.items if item.kind.startswith(_ALWAYS_INCLUDE_KIND_PREFIX)
    ]

    sampled: list[EvidenceItem] = []
    counts: dict[str, int] = defaultdict(int)
    for item in pack.items:
        if item.kind.startswith(_ALWAYS_INCLUDE_KIND_PREFIX):
            continue
        if len(sampled) >= _MAX_SAMPLED_ITEMS:
            break
        if counts[item.kind] >= _PER_KIND_BUDGET:
            continue
        counts[item.kind] += 1
        sampled.append(item)

    items = tuple(always_include + sampled)
    total = len(pack.items)
    return CuratedEvidence(
        items=items, excluded_count=max(0, total - len(items)), total_candidates=total
    )


def render_curated_evidence_text(curated: CuratedEvidence) -> str:
    if not curated.items:
        return "No repository evidence is available."

    lines = [
        f"id={item.id} kind={item.kind} locator={item.reference.locator}: {item.raw_value}"
        for item in curated.items
    ]
    text = "\n".join(lines)
    if curated.excluded_count:
        text += (
            f"\n\n({curated.excluded_count} further evidence item(s) omitted for brevity, "
            f"out of {curated.total_candidates} total.)"
        )
    return text
