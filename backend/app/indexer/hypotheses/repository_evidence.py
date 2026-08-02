"""ADR 0018 Frontier Hypothesis Generator — Finding 1's resolution: a
small, generator-agnostic repository-evidence extraction stage that runs
while the cloned repository still exists, so evidence that isn't
recoverable from an `ArchitectureModel` (README content, manifest
snippets, top-level repository metadata) still reaches the same
`EngineeringEvidencePack` every generator reads.

Deliberately not LLM-specific: this module knows nothing about hypothesis
generation, prompts, or the LLM generator at all — it produces
`EvidenceItem`s the same way `deterministic_generator.py`'s node/edge
conversion does, for any current or future generator to read
(`HypothesisGenerator.generate`'s only input is the pack; this is what
puts repository-level facts into it). Treats README files, manifests,
architecture documents, and configuration files uniformly as "repository
artifacts" via one small, explicit, safe allowlist — not a recursive
directory scan — for two reasons: (1) bounded, predictable evidence size
(a handful of small text blobs, not an arbitrary fraction of the
repository), and (2) safety — scanning every file risks reading a real
secret (a committed `.env`, a private key) into a persisted evidence blob
and, eventually, an LLM prompt. `.env`/key/credential-shaped filenames are
explicitly never read, even if a future artifact type is added to the
allowlist.

Every fact's `raw_value` is truncated to `_MAX_CONTENT_CHARS` — the
evidence pack stores full fidelity for what it captures (RFC-02's
lossless node/edge conversion), but a repository-level document has no
natural size bound the way a parsed AST fact does, and an unbounded README
would dominate both storage and, later, prompt size for no proportional
information gain.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.knowledge_engine.contracts.evidence import EvidenceItem, EvidenceReference
from app.knowledge_engine.contracts.provenance import GeneratorIdentity, Provenance

_MAX_CONTENT_CHARS = 3000
_MAX_LISTING_ENTRIES = 100

_README_FILENAMES = ("README.md", "README.rst", "README.txt", "README")
_ARCHITECTURE_DOC_FILENAMES = (
    "ARCHITECTURE.md",
    "docs/architecture.md",
    "docs/ARCHITECTURE.md",
)
_MANIFEST_FILENAMES = (
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "package.json",
    "requirements.txt",
    "pyproject.toml",
    "go.mod",
    "Cargo.toml",
    "Gemfile",
    "composer.json",
)
_CONFIG_FILENAMES = (
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
)

# Never read, even though some of these directory names look like they'd
# match a future artifact type — filenames that commonly hold real
# secrets. Checked against the file's own basename, not the allowlist
# above (which never contains these names), as a second, independent
# guard against a future allowlist addition making this unsafe by
# accident.
_NEVER_READ_BASENAME_PREFIXES = (".env", "id_rsa", "credentials", "secret")
_NEVER_READ_SUFFIXES = (".pem", ".key", ".pfx", ".p12")

_REPOSITORY_EVIDENCE_IDENTITY = GeneratorIdentity(
    kind="deterministic", name="repository_evidence_extractor", version="1.0.0"
)


@dataclass(frozen=True)
class RepositoryEvidenceFact:
    """A plain-value fact, no `Provenance` yet — mirrors
    `app.indexer.services.indexing_service.index_repository`'s own "plain
    values, not typed contract objects yet" precedent for exactly the same
    reason: this is produced while the clone is alive, and converted into
    real `EvidenceItem`s later (`repository_evidence_facts_to_items`),
    once the containing pack's id is known (see this module's caller,
    `shadow_runner.py`)."""

    kind: str
    locator: str
    raw_value: str


def _is_safe_to_read(relative_path: str) -> bool:
    basename = Path(relative_path).name.lower()
    if basename.startswith(_NEVER_READ_BASENAME_PREFIXES):
        return False
    return not basename.endswith(_NEVER_READ_SUFFIXES)


def _read_truncated(path: Path) -> str | None:
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if len(content) > _MAX_CONTENT_CHARS:
        return content[:_MAX_CONTENT_CHARS] + "\n... (truncated)"
    return content


def _facts_for_filenames(
    repo_path: Path, filenames: tuple[str, ...], kind: str
) -> list[RepositoryEvidenceFact]:
    facts: list[RepositoryEvidenceFact] = []
    for relative_name in filenames:
        if not _is_safe_to_read(relative_name):
            continue
        candidate = repo_path / relative_name
        if not candidate.is_file():
            continue
        content = _read_truncated(candidate)
        if content is None or not content.strip():
            continue
        facts.append(RepositoryEvidenceFact(kind=kind, locator=relative_name, raw_value=content))
    return facts


def _repository_metadata_fact(repo_path: Path) -> RepositoryEvidenceFact:
    entries = sorted(p.name for p in repo_path.iterdir())[:_MAX_LISTING_ENTRIES]
    return RepositoryEvidenceFact(
        kind="repository_metadata",
        locator=".",
        raw_value=json.dumps({"top_level_entries": entries}, sort_keys=True),
    )


def extract_repository_evidence(repo_path: Path) -> list[RepositoryEvidenceFact]:
    """Every repository-level fact this stage can safely recover, read
    once while `repo_path` still exists. Returns an empty-ish list (just
    the metadata fact) for a repository with none of the known artifact
    filenames — absence of a README/manifest is not an error, it's simply
    nothing further to report."""
    facts = [_repository_metadata_fact(repo_path)]
    facts.extend(_facts_for_filenames(repo_path, _README_FILENAMES, "repository_readme"))
    facts.extend(
        _facts_for_filenames(repo_path, _ARCHITECTURE_DOC_FILENAMES, "repository_architecture_doc")
    )
    facts.extend(_facts_for_filenames(repo_path, _MANIFEST_FILENAMES, "repository_manifest"))
    facts.extend(_facts_for_filenames(repo_path, _CONFIG_FILENAMES, "repository_config"))
    return facts


def repository_evidence_facts_to_items(
    facts: list[RepositoryEvidenceFact],
    *,
    repository_id: str,
    commit_sha: str,
    pack_id: str,
) -> list[EvidenceItem]:
    """Converts the plain-value facts into real `EvidenceItem`s once the
    containing pack's id is known (see this module's docstring for why
    this is a separate step from extraction itself). Reliability tier 3
    (deterministic) — same as `deterministic_generator.py`'s node/edge
    items: this is a literal, verified capture of a real file's real
    content, not a guess. Whatever *claim* an LLM later infers from it is
    a separate, much less certain thing, tracked entirely by that
    generator's own `generator_confidence` and, eventually, the
    validators/confidence engine — never by this evidence item's own
    reliability."""
    provenance = Provenance(
        generator=_REPOSITORY_EVIDENCE_IDENTITY,
        produced_at=datetime.now(UTC),
        pack_id=pack_id,
        pack_version="v1",
        run_id=pack_id,
    )
    items: list[EvidenceItem] = []
    for fact in facts:
        items.append(
            EvidenceItem(
                id=f"evidence:{repository_id}:repo:{fact.kind}:{fact.locator}",
                kind=fact.kind,
                source_type="documentation",
                reliability_tier=3,
                reference=EvidenceReference(
                    repository_id=repository_id,
                    source_type="documentation",
                    locator=fact.locator,
                    key=fact.locator,
                    commit_sha=commit_sha,
                ),
                raw_value=fact.raw_value,
                provenance=provenance,
            )
        )
    return items
