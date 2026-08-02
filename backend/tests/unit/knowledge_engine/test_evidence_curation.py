"""ADR 0018 Frontier Hypothesis Generator — `evidence_curation.curate_for_prompt`."""

from __future__ import annotations

from datetime import UTC, datetime

from app.knowledge_engine.contracts.evidence import (
    EngineeringEvidencePack,
    EvidenceItem,
    EvidenceReference,
)
from app.knowledge_engine.contracts.provenance import GeneratorIdentity, Provenance
from app.knowledge_engine.evidence_curation import curate_for_prompt, render_curated_evidence_text


def _provenance() -> Provenance:
    return Provenance(
        generator=GeneratorIdentity(kind="deterministic", name="test", version="1.0.0"),
        produced_at=datetime.now(UTC),
        pack_id="pack:1",
        pack_version="v1",
        run_id="pack:1",
    )


def _item(item_id: str, kind: str, raw_value: str = "value") -> EvidenceItem:
    return EvidenceItem(
        id=item_id,
        kind=kind,
        source_type="code",
        reliability_tier=3,
        reference=EvidenceReference(repository_id="repo-1", source_type="code", locator=item_id),
        raw_value=raw_value,
        provenance=_provenance(),
    )


def _pack(items: list[EvidenceItem]) -> EngineeringEvidencePack:
    return EngineeringEvidencePack(
        id="pack:1",
        repository_id="repo-1",
        commit_sha="abc",
        schema_version="v1",
        items=tuple(items),
    )


def test_repository_kind_items_always_included_uncapped() -> None:
    items = [_item(f"evidence:readme:{i}", "repository_readme") for i in range(10)]
    curated = curate_for_prompt(_pack(items))

    assert len(curated.items) == 10
    assert curated.excluded_count == 0


def test_non_repository_kinds_capped_per_kind() -> None:
    items = [_item(f"evidence:node:{i}", "graph_node:Component") for i in range(20)]
    curated = curate_for_prompt(_pack(items))

    assert len(curated.items) == 5
    assert curated.excluded_count == 15


def test_repository_kinds_never_counted_against_the_sampled_cap() -> None:
    items = [_item("evidence:readme:0", "repository_readme")] + [
        _item(f"evidence:node:{i}", "graph_node:Component") for i in range(5)
    ]
    curated = curate_for_prompt(_pack(items))

    kinds_included = [item.kind for item in curated.items]
    assert "repository_readme" in kinds_included
    assert kinds_included.count("graph_node:Component") == 5


def test_render_empty_pack_is_explicit() -> None:
    curated = curate_for_prompt(_pack([]))
    text = render_curated_evidence_text(curated)
    assert "No repository evidence" in text


def test_render_includes_ids_for_citation() -> None:
    curated = curate_for_prompt(_pack([_item("evidence:readme:0", "repository_readme", "hello")]))
    text = render_curated_evidence_text(curated)
    assert "id=evidence:readme:0" in text
    assert "hello" in text
