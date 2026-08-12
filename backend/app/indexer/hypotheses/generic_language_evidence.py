"""RFC-07 — deterministic evidence for a repository whose language has no
`ILanguageParser` (`get_parser()` returned None). This is the "relax the
hard 422 gate" half of ADR 0018's RFC-07 roadmap entry: instead of
refusing to index the repository at all, produce what a parser-less
repository can still honestly support - that it exists, which files it
contains, a best-effort (heuristic, not deterministic-tier) guess at
function/method-like declarations within them, and (separately, as raw
evidence, not yet a claim about relationships) a bounded sample of each
file's content for `generic_language_generator.py`'s LLM generator to
read.

Two reliability tiers, deliberately not conflated:
- File existence (`SourceFile` nodes): fully deterministic
  (`_DETERMINISTIC_TIER`, same tier `deterministic_generator.py` uses for
  parser-derived nodes) - a file being present at a given path in the
  clone is an observed fact, not an inference.
- Declaration-like symbol detection (`GenericSymbol` nodes, see
  `_DECLARATION_PATTERN`): heuristic (`_HEURISTIC_TIER`) - one shared
  regex, applied identically regardless of language, catching the common
  `func`/`function`/`def`/`fn NAME(` shape several languages share. This
  is NOT a parser and does not claim to be: it can miss real declarations
  (any syntax outside that shape) and can false-positive (matching inside
  a string or comment) - exactly why it's tier 1, not tier 3, and why its
  existence alone never promotes a hypothesis (see
  `knowledge_engine/validators/generic_structural.py`'s own reasoning).
  Having *some* named entities for the LLM/validators to reason about
  beyond whole files is what makes a `CALLS` relationship (as opposed to
  only file-level `IMPORTS`) representable at all - without it, "function
  A calls function B" has no `graph_node` for either endpoint to ground
  against.

What the file/symbol *means* (does it import that other file? call this
function?) is a claim, not an observation, and belongs entirely to
`generic_language_generator.py`'s `Hypothesis`es, gated through the same
validation/confidence pipeline as any other generator's claims - this
module produces zero relationship evidence and zero hypotheses.

Bounded by design (ADR 0018's cost-control discipline): `_MAX_FILES`/
`_MAX_BYTES_PER_FILE` are hard caps, not heuristics - files/bytes beyond
them are simply not sampled. Raised from the original prototype's 40/4KB
to 150/6KB this cycle specifically because the LLM generator now batches
(see `generic_language_generator.py`'s `_BATCH_SIZE`/`_MAX_BATCHES`)
instead of sending every candidate file in one prompt - the actual
per-request size stays bounded either way; only the *total* repository
coverage grew.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path

from app.indexer.scanner.language_registry import describe_language
from app.indexer.scanner.skip_directories import SKIP_DIRECTORIES
from app.knowledge_engine.contracts.evidence import (
    EngineeringEvidencePack,
    EvidenceItem,
    EvidenceReference,
)
from app.knowledge_engine.contracts.provenance import GeneratorIdentity, Provenance

_DETERMINISTIC_TIER = 3
_HEURISTIC_TIER = 1
_NODE_KIND_PREFIX = "graph_node"
_SOURCE_FILE_EVIDENCE_KIND = "source_file"
_DISCOVERY_SOURCE = "generic_fallback"
_GENERATOR_IDENTITY = GeneratorIdentity(
    kind="deterministic", name="generic_file_discovery", version="1.1.0"
)

# Cost-control caps - deliberately bounded, not a replacement indexing
# strategy: proving "some accurate, low-cost structure beats none" is the
# goal, not full-fidelity extraction. See module docstring for why these
# were raised from the original prototype's 40/4KB.
_MAX_FILES = 150
_MAX_BYTES_PER_FILE = 6_000

_NON_SOURCE_EXTENSIONS = frozenset(
    {
        ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".pdf", ".zip", ".tar", ".gz",
        ".woff", ".woff2", ".ttf", ".eot", ".mp4", ".mp3", ".wav", ".lock", ".sum",
    }
)

# One shared pattern, applied to every candidate file regardless of
# language - see module docstring's "heuristic, not a parser" note. Named
# capture group unused deliberately; group(1) is always the declared name.
_DECLARATION_PATTERN = re.compile(
    r"\b(?:func|function|def|fn)\s+([A-Za-z_][A-Za-z0-9_]*)\s*\("
)
_MAX_SYMBOLS_PER_FILE = 25


def _select_candidate_files(repo_root: Path) -> list[str]:
    """Every non-skipped, non-binary-looking file, up to `_MAX_FILES`, in a
    stable (sorted) order - determinism matters here (the same repository
    state must always select the same files, so re-running this evidence
    step is reproducible, matching ADR 0018's reproducibility requirement
    for evidence)."""
    candidates: list[str] = []
    for path in sorted(repo_root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRECTORIES for part in path.parts):
            continue
        if path.suffix.lower() in _NON_SOURCE_EXTENSIONS:
            continue
        candidates.append(str(path.relative_to(repo_root)))
        if len(candidates) >= _MAX_FILES:
            break
    return candidates


def _read_bounded(path: Path) -> str:
    try:
        raw = path.read_bytes()[:_MAX_BYTES_PER_FILE]
    except OSError:
        return ""
    return raw.decode("utf-8", errors="replace")


def content_hash(text: str) -> str:
    """Deterministic, short content fingerprint - used both as part of
    each `source_file`/`SourceFile` evidence item's identity (so identical
    content on a later run produces byte-identical evidence, ADR 0018's
    reproducibility requirement) and by
    `generic_language_generator.py`'s batch-level LLM-call cache (skip
    re-analyzing a batch whose every file hashes identically to the prior
    run's)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _extract_symbols(text: str) -> list[str]:
    seen: list[str] = []
    for match in _DECLARATION_PATTERN.finditer(text):
        name = match.group(1)
        if name not in seen:
            seen.append(name)
        if len(seen) >= _MAX_SYMBOLS_PER_FILE:
            break
    return seen


def discover_generic_language_evidence(
    *,
    repository_id: str,
    commit_sha: str,
    repo_root: Path,
    language_label: str,
    repository_name: str | None = None,
) -> EngineeringEvidencePack:
    """The generic-fallback counterpart to
    `deterministic_generator.architecture_model_to_evidence_pack` - same
    node-id scheme (`f"{repository_id}:{kind}:{key}"`, ADR 0007), same
    `graph_node:*` evidence-item kind convention the Materializer already
    reads, so it requires zero new Materializer code to turn these nodes
    into real graph nodes. `source_file` evidence items exist purely to
    feed `generic_language_generator.py`'s LLM generator - the Materializer
    never reads that kind at all.

    `language_label` is a fallback display value only (the caller's own
    `DetectedLanguage` value, stringified) - `language_registry.describe_language`
    is consulted first and wins whenever it recognizes the repository's
    files, purely for a more informative Repository node/logs; nothing
    about the generic pipeline's behavior depends on which label wins.
    """
    candidate_paths = _select_candidate_files(repo_root)
    descriptor = describe_language(repo_root)
    if descriptor.name != "unsupported":
        language_label = descriptor.name

    pack_id = f"pack:{repository_id}:{commit_sha}:{_GENERATOR_IDENTITY.name}"
    provenance = Provenance(
        generator=_GENERATOR_IDENTITY,
        produced_at=datetime.now(UTC),
        pack_id=pack_id,
        pack_version="v1",
        run_id=pack_id,
    )

    repo_node_id = f"{repository_id}:repository"
    items: list[EvidenceItem] = [
        EvidenceItem(
            id=f"ev:node:{repo_node_id}",
            kind=f"{_NODE_KIND_PREFIX}:Repository",
            source_type="code",
            reliability_tier=_DETERMINISTIC_TIER,
            reference=EvidenceReference(
                repository_id=repository_id,
                source_type="code",
                locator=repo_node_id,
                key=repo_node_id,
                commit_sha=commit_sha,
            ),
            raw_value=json.dumps(
                {
                    "language": language_label,
                    "framework": "",
                    "discovery_source": _DISCOVERY_SOURCE,
                    **({"name": repository_name} if repository_name else {}),
                },
                sort_keys=True,
            ),
            provenance=provenance,
        )
    ]

    for rel_path in candidate_paths:
        node_id = f"{repository_id}:source-file:{rel_path}"
        text = _read_bounded(repo_root / rel_path)
        digest = content_hash(text)
        reference = EvidenceReference(
            repository_id=repository_id,
            source_type="code",
            locator=rel_path,
            key=node_id,
            commit_sha=commit_sha,
        )
        items.append(
            EvidenceItem(
                id=f"ev:node:{node_id}",
                kind=f"{_NODE_KIND_PREFIX}:Component:SourceFile",
                source_type="code",
                reliability_tier=_DETERMINISTIC_TIER,
                reference=reference,
                raw_value=json.dumps(
                    {
                        "name": rel_path,
                        "file_path": rel_path,
                        "language": language_label,
                        "component_type": "SourceFile",
                        "discovery_source": _DISCOVERY_SOURCE,
                        "content_hash": digest,
                    },
                    sort_keys=True,
                ),
                provenance=provenance,
            )
        )
        items.append(
            EvidenceItem(
                id=f"ev:source:{node_id}",
                kind=_SOURCE_FILE_EVIDENCE_KIND,
                source_type="code",
                reliability_tier=_DETERMINISTIC_TIER,
                reference=reference,
                raw_value=text,
                provenance=provenance,
            )
        )

        for symbol_name in _extract_symbols(text):
            symbol_node_id = f"{repository_id}:generic-symbol:{rel_path}:{symbol_name}"
            items.append(
                EvidenceItem(
                    id=f"ev:node:{symbol_node_id}",
                    kind=f"{_NODE_KIND_PREFIX}:Component:GenericSymbol",
                    source_type="code",
                    reliability_tier=_HEURISTIC_TIER,
                    reference=EvidenceReference(
                        repository_id=repository_id,
                        source_type="code",
                        locator=symbol_name,
                        key=symbol_node_id,
                        commit_sha=commit_sha,
                    ),
                    raw_value=json.dumps(
                        {
                            "name": symbol_name,
                            "file_path": rel_path,
                            "language": language_label,
                            "component_type": "GenericSymbol",
                            "discovery_source": f"{_DISCOVERY_SOURCE}_heuristic",
                        },
                        sort_keys=True,
                    ),
                    provenance=provenance,
                )
            )

    return EngineeringEvidencePack(
        id=pack_id,
        repository_id=repository_id,
        commit_sha=commit_sha,
        schema_version="v1",
        items=tuple(items),
    )
