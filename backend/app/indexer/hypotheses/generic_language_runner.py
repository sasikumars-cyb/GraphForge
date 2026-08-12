"""RFC-07 — orchestrates the fallback pipeline for a repository whose
language has no `ILanguageParser`: generate evidence (file discovery,
deterministic) -> generate hypotheses (the generic LLM generator, batched
and cached here) -> validate -> compute confidence -> persist to
Engineering Memory. The exact same four downstream stages
`shadow_runner.run_shadow_hypothesis_generation` already uses
(`reason_hypotheses`, `DefaultConfidenceEngine`, `EngineeringMemoryService`)
- reused directly, not re-implemented - because this repository's evidence
doesn't come from an `ArchitectureModel` at all, so it can't share that
function's own pack-construction step, but everything downstream of "here
is a pack" is identical.

Unlike `run_shadow_hypothesis_generation` (always shadow, by design, at
least until an explicit cutover elsewhere), this function's caller
(`indexing_service.py`) is the ONLY path that can ever reach it, and only
when both (a) no parser exists for the detected language and (b) the
feature is explicitly enabled (`Settings.enable_generic_language_fallback`,
off by default - a real LLM call and a new external-dependency failure
mode, same cost-control precedent `enable_frontier_llm_generator` already
set). There is no "direct write" fallback available for this path the way
`index_repository`'s authoritative-mode fallback has one (`graph.builder`
has nothing to build without an `ArchitectureModel`) - the caller must
treat a `None` return (or an exception) as "nothing to write" and behave
accordingly, never inventing a graph.

Batching and content-hash caching (RFC-07 scale-out this cycle) live HERE,
not inside the generator: `HypothesisGenerator.generate(pack)` stays a
pure, DB-free, single-pack function (the existing contract every generator
in this codebase already honors), and only the orchestrating runner has
(or needs) database access to look up the prior run's evidence. A file's
own `SourceFile` node carries a `content_hash` (see
`generic_language_evidence.content_hash`) precisely so this cache
comparison never has to re-read file bytes - it's a pure pack-to-pack
comparison, `O(files)`, no filesystem I/O beyond what evidence discovery
already did once this run.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.indexer.hypotheses.generic_language_evidence import discover_generic_language_evidence
from app.indexer.hypotheses.generic_language_generator import (
    GenericLanguageHypothesisGenerator,
    build_generic_language_hypothesis_generator,
)
from app.indexer.hypotheses.shadow_runner import reason_hypotheses
from app.knowledge_engine.confidence.default_engine import DefaultConfidenceEngine
from app.knowledge_engine.contracts.evidence import EngineeringEvidencePack, EvidenceItem
from app.knowledge_engine.contracts.explanation import ConfidenceExplanation
from app.knowledge_engine.contracts.knowledge import KnowledgeRelationship
from app.knowledge_engine.memory_service import EngineeringMemoryService

logger = logging.getLogger(__name__)

_NODE_KIND_PREFIX = "graph_node"
# Mirrors `generic_language_evidence._SOURCE_FILE_EVIDENCE_KIND` - not
# imported directly since that name is that module's own private
# implementation detail; this runner only needs to recognize the same
# evidence-item kind, not depend on the module's internals.
_SOURCE_FILE_EVIDENCE_KIND = "source_file"

# One LLM call is scoped to at most this many files' worth of evidence -
# matches `generic_language_generator._BATCH_SIZE`; kept as a separate
# constant here (rather than importing that one) because this is the
# runner's OWN batching policy - the generator has no opinion on how many
# times it gets called, only on how to handle whatever single pack it's
# given each time.
_BATCH_SIZE = 20


def _file_locator(item: EvidenceItem) -> str:
    return item.reference.locator or ""


def _split_into_batches(pack: EngineeringEvidencePack) -> list[EngineeringEvidencePack]:
    """Splits one repository-wide evidence pack into several smaller packs,
    each carrying at most `_BATCH_SIZE` files' `source_file` items (plus
    that file's own `SourceFile`/`GenericSymbol` graph_node items) - this
    is what keeps a single LLM call's prompt bounded regardless of how many
    files `generic_language_evidence._MAX_FILES` allows across the whole
    repository. Non-file items (the `Repository` node) are included in
    every batch unchanged; they're small and each batch's generator run
    doesn't use them anyway (`GenericLanguageHypothesisGenerator.generate`
    only reads `source_file`/`GenericSymbol` items)."""
    source_items = [item for item in pack.items if item.kind == _SOURCE_FILE_EVIDENCE_KIND]
    if len(source_items) <= _BATCH_SIZE:
        return [pack]

    other_items = [item for item in pack.items if item.kind != _SOURCE_FILE_EVIDENCE_KIND]
    node_by_locator: dict[str, list[EvidenceItem]] = {}
    for item in other_items:
        node_by_locator.setdefault(_file_locator(item), []).append(item)

    batches: list[EngineeringEvidencePack] = []
    for batch_start in range(0, len(source_items), _BATCH_SIZE):
        batch_source_items = source_items[batch_start : batch_start + _BATCH_SIZE]
        batch_locators = {_file_locator(item) for item in batch_source_items}
        batch_extra_items = [
            item
            for locator in batch_locators
            for item in node_by_locator.get(locator, [])
        ]
        repo_level_items = node_by_locator.get("", [])
        batch_items = (*repo_level_items, *batch_extra_items, *batch_source_items)
        batches.append(
            EngineeringEvidencePack(
                id=f"{pack.id}:batch{batch_start // _BATCH_SIZE}",
                repository_id=pack.repository_id,
                commit_sha=pack.commit_sha,
                schema_version=pack.schema_version,
                items=tuple(batch_items),
            )
        )
    return batches


def _content_hash_fingerprint(pack: EngineeringEvidencePack) -> dict[str, str]:
    """`{file_path: content_hash}` for every `SourceFile` node in a pack -
    the comparable shape both a prior run's stored pack and this run's
    fresh pack reduce to, so "did this batch's files change since the
    prior run" is a plain dict comparison, not a semantic diff."""
    fingerprint: dict[str, str] = {}
    for item in pack.items:
        if item.kind != f"{_NODE_KIND_PREFIX}:Component:SourceFile":
            continue
        try:
            payload = json.loads(item.raw_value)
        except json.JSONDecodeError:
            continue
        file_path = payload.get("file_path")
        digest = payload.get("content_hash")
        if isinstance(file_path, str) and isinstance(digest, str):
            fingerprint[file_path] = digest
    return fingerprint


def _batch_unchanged(batch: EngineeringEvidencePack, prior_fingerprint: dict[str, str]) -> bool:
    """True only when EVERY file this batch would analyze already has an
    identical `content_hash` in the prior run's pack - a single changed or
    new file in the batch is enough to re-run the LLM call for it (never
    skip on a partial match; under-caching costs an extra LLM call,
    over-caching would silently miss a real code change)."""
    batch_fingerprint = _content_hash_fingerprint(batch)
    if not batch_fingerprint:
        return False
    return all(prior_fingerprint.get(path) == digest for path, digest in batch_fingerprint.items())


async def _load_prior_fingerprint(
    memory: EngineeringMemoryService, repository_id: str
) -> dict[str, str]:
    """Best-effort lookup of the immediately preceding run's evidence pack
    for this repository, reduced to a content-hash fingerprint. Any
    failure (no prior pack, storage error, malformed data) is treated
    identically to "no prior run" - caching is a pure optimization, never
    something correctness depends on, so the safe default on any doubt is
    "assume changed, run the LLM call"."""
    try:
        records = await memory.list_evidence_packs(
            uuid.UUID(repository_id), exclude_commit_sha="n/a-cross-repo", limit=1
        )
        if not records:
            return {}
        prior_pack = await memory.retrieve_evidence_pack(records[0].pack_id)
    except Exception:
        logger.debug(
            "generic_language_prior_pack_lookup_failed repository_id=%s",
            repository_id,
            exc_info=True,
        )
        return {}
    if prior_pack is None:
        return {}
    return _content_hash_fingerprint(prior_pack)


async def _generate_with_batching(
    generator: GenericLanguageHypothesisGenerator,
    pack: EngineeringEvidencePack,
    prior_fingerprint: dict[str, str],
) -> tuple[list, int]:
    """Runs the generator once per batch, skipping any batch whose files
    are all unchanged since the prior run. Returns `(hypotheses,
    skipped_batch_count)` - the skip count is surfaced only for logging,
    never used to alter validation/confidence behavior."""
    batches = _split_into_batches(pack)
    hypotheses: list = []
    skipped = 0
    for batch in batches:
        if prior_fingerprint and _batch_unchanged(batch, prior_fingerprint):
            skipped += 1
            continue
        hypotheses.extend(await generator.generate(batch))
    return hypotheses, skipped


async def run_generic_language_fallback(
    *,
    repository_id: str,
    commit_sha: str,
    repo_root: Path,
    language_label: str,
    db: AsyncSession,
    repository_name: str | None = None,
) -> EngineeringEvidencePack | None:
    """Runs the full generate -> validate -> confidence -> persist pipeline
    for a repository with no deterministic parser. Returns the evidence
    pack on success (so the caller can log/summarize file counts, the same
    shape `IndexingSummary` already reports for parser-based runs) or
    `None` if the pipeline itself failed - logged here, never raised, same
    "never fail indexing over this" discipline
    `run_shadow_hypothesis_generation` already follows, except this *is*
    the only path to a graph for this repository, so the caller must
    still report an honest, empty-graph outcome rather than pretending
    indexing partially succeeded.

    Content-hash caching (see `_load_prior_fingerprint`/
    `_generate_with_batching`) means an unchanged repository's re-index can
    skip every LLM call and still persist the same evidence/graph - it does
    NOT yet mean this is a true incremental *validation* pipeline: a
    skipped batch contributes zero new hypotheses this run, so any
    relationship whose confidence previously depended on it simply keeps
    its last-computed value in Engineering Memory rather than being
    recomputed. See the RFC-07 follow-up report for why full incremental
    revalidation (re-deriving confidence itself, not just skipping
    generation) is intentionally out of scope this cycle.
    """
    started_at = time.monotonic()
    try:
        pack = discover_generic_language_evidence(
            repository_id=repository_id,
            commit_sha=commit_sha,
            repo_root=repo_root,
            language_label=language_label,
            repository_name=repository_name,
        )
    except Exception:
        logger.exception(
            "generic_language_evidence_discovery_failed repository_id=%s", repository_id
        )
        return None

    engine = DefaultConfidenceEngine()
    memory = EngineeringMemoryService(db)
    all_relationships: list[KnowledgeRelationship] = []
    all_explanations: list[ConfidenceExplanation] = []
    total_hypotheses = 0
    total_confirms = 0
    total_contradicts = 0
    skipped_batches = 0

    try:
        prior_fingerprint = await _load_prior_fingerprint(memory, repository_id)
        generator = build_generic_language_hypothesis_generator()
        hypotheses, skipped_batches = await _generate_with_batching(
            generator, pack, prior_fingerprint
        )
        total_hypotheses = len(hypotheses)
        all_relationships, all_explanations, total_confirms, total_contradicts = (
            await reason_hypotheses(
                hypotheses,
                pack,
                engine,
                repository_id=repository_id,
                generator_name=generator.identity.name,
            )
        )
    except Exception:
        # The LLM generator failing (unreachable provider, no API key
        # configured) must not discard the evidence already gathered -
        # file-level structure (Repository + SourceFile nodes) is still
        # real, deterministic, and worth persisting/materializing even
        # with zero relationships proposed this run.
        logger.exception("generic_language_generator_failed repository_id=%s", repository_id)

    try:
        await memory.store_evidence_pack(uuid.UUID(repository_id), pack)
        if all_relationships:
            await memory.store_relationships(
                uuid.UUID(repository_id), all_relationships, all_explanations
            )
    except Exception:
        logger.exception("generic_language_persistence_failed repository_id=%s", repository_id)
        return None

    elapsed_seconds = time.monotonic() - started_at
    logger.info(
        "generic_language_fallback_succeeded repository_id=%s language=%s "
        "evidence_count=%d hypothesis_count=%d validated_relationship_count=%d "
        "confirmations=%d contradictions=%d skipped_batches=%d elapsed_seconds=%.3f",
        repository_id,
        language_label,
        len(pack.items),
        total_hypotheses,
        len(all_relationships),
        total_confirms,
        total_contradicts,
        skipped_batches,
        elapsed_seconds,
    )
    return pack
