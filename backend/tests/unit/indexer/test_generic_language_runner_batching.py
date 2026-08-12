"""RFC-07 — pure, DB-free unit tests for `generic_language_runner.py`'s
batching and content-hash-caching helpers. Persistence-touching behavior
(`_load_prior_fingerprint`, the full `run_generic_language_fallback`
pipeline) is covered by `tests/integration/test_generic_language_fallback.py`
instead, against a real Postgres/Neo4j - these tests only exercise the
parts that need neither."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from app.indexer.hypotheses.generic_language_runner import (
    _BATCH_SIZE,
    _batch_unchanged,
    _content_hash_fingerprint,
    _split_into_batches,
)
from app.knowledge_engine.contracts.evidence import (
    EngineeringEvidencePack,
    EvidenceItem,
    EvidenceReference,
)
from app.knowledge_engine.contracts.provenance import GeneratorIdentity, Provenance


def _provenance() -> Provenance:
    return Provenance(
        generator=GeneratorIdentity(kind="deterministic", name="generic_file_discovery", version="1.1.0"),
        produced_at=datetime.now(UTC),
        pack_id="pack:repo-1:abc:generic_file_discovery",
        pack_version="v1",
        run_id="pack:repo-1:abc:generic_file_discovery",
    )


def _source_file_node_item(path: str, digest: str) -> EvidenceItem:
    node_id = f"repo-1:source-file:{path}"
    return EvidenceItem(
        id=f"ev:node:{node_id}",
        kind="graph_node:Component:SourceFile",
        source_type="code",
        reliability_tier=3,
        reference=EvidenceReference(
            repository_id="repo-1", source_type="code", locator=path, key=node_id, commit_sha="abc"
        ),
        raw_value=json.dumps(
            {"name": path, "file_path": path, "language": "go", "content_hash": digest}, sort_keys=True
        ),
        provenance=_provenance(),
    )


def _source_file_text_item(path: str, content: str) -> EvidenceItem:
    node_id = f"repo-1:source-file:{path}"
    return EvidenceItem(
        id=f"ev:source:{node_id}",
        kind="source_file",
        source_type="code",
        reliability_tier=3,
        reference=EvidenceReference(
            repository_id="repo-1", source_type="code", locator=path, key=node_id, commit_sha="abc"
        ),
        raw_value=content,
        provenance=_provenance(),
    )


def _file(path: str, digest: str, content: str) -> tuple[EvidenceItem, EvidenceItem]:
    return _source_file_node_item(path, digest), _source_file_text_item(path, content)


def _pack(items: list[EvidenceItem]) -> EngineeringEvidencePack:
    return EngineeringEvidencePack(
        id="pack:repo-1:abc:generic_file_discovery",
        repository_id="repo-1",
        commit_sha="abc",
        schema_version="v1",
        items=tuple(items),
    )


def test_a_pack_at_or_under_the_batch_size_is_a_single_batch() -> None:
    items = [item for i in range(_BATCH_SIZE) for item in _file(f"f{i}.go", "h", "text")]
    pack = _pack(items)
    batches = _split_into_batches(pack)
    assert len(batches) == 1
    assert batches[0] is pack


def test_a_pack_over_the_batch_size_splits_into_multiple_batches() -> None:
    items = [item for i in range(_BATCH_SIZE + 5) for item in _file(f"f{i}.go", "h", "text")]
    pack = _pack(items)
    batches = _split_into_batches(pack)
    assert len(batches) == 2

    source_items_per_batch = [
        len([i for i in b.items if i.kind == "source_file"]) for b in batches
    ]
    assert source_items_per_batch == [_BATCH_SIZE, 5]


def test_each_batch_carries_only_its_own_files_node_evidence() -> None:
    """A file's `SourceFile` node evidence must travel with its own batch,
    not leak into every batch - otherwise a later batch's LLM call could
    "see" (and potentially cite) a file it was never actually given the
    content of."""
    items = [item for i in range(_BATCH_SIZE + 1) for item in _file(f"f{i}.go", "h", "text")]
    pack = _pack(items)
    batches = _split_into_batches(pack)

    first_batch_paths = {
        i.reference.locator for i in batches[0].items if i.kind == "graph_node:Component:SourceFile"
    }
    second_batch_paths = {
        i.reference.locator for i in batches[1].items if i.kind == "graph_node:Component:SourceFile"
    }
    assert first_batch_paths.isdisjoint(second_batch_paths)
    assert len(first_batch_paths) == _BATCH_SIZE
    assert len(second_batch_paths) == 1


def test_content_hash_fingerprint_maps_file_path_to_hash() -> None:
    node_item, _ = _file("main.go", "abc123", "package main")
    pack = _pack([node_item])
    assert _content_hash_fingerprint(pack) == {"main.go": "abc123"}


def test_batch_unchanged_true_when_every_file_hash_matches_prior_run() -> None:
    node_item, text_item = _file("main.go", "abc123", "package main")
    batch = _pack([node_item, text_item])
    assert _batch_unchanged(batch, {"main.go": "abc123"}) is True


def test_batch_unchanged_false_when_any_file_hash_differs() -> None:
    node_item, text_item = _file("main.go", "abc123", "package main")
    batch = _pack([node_item, text_item])
    assert _batch_unchanged(batch, {"main.go": "different-hash"}) is False


def test_batch_unchanged_false_when_prior_run_never_saw_this_file() -> None:
    node_item, text_item = _file("new_file.go", "abc123", "package main")
    batch = _pack([node_item, text_item])
    assert _batch_unchanged(batch, {"main.go": "abc123"}) is False


def test_batch_unchanged_false_for_an_empty_batch_fingerprint() -> None:
    """An empty batch (no SourceFile node evidence at all) is never treated
    as \"unchanged\" - there is nothing to compare, so the safe default is
    to not skip it."""
    assert _batch_unchanged(_pack([]), {"main.go": "abc123"}) is False
