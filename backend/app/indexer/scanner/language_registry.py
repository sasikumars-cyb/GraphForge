"""RFC-07 — metadata-driven language/artifact detection, replacing
`generic_language_evidence.py`'s old flat `_EXTENSION_LABELS` lookup with
a proper, extensible registry that also reports which extraction paths a
detected language actually has available.

Adding a new language is one `LanguageSpec` entry (a data tuple) - never a
change to the scan loop (`_scan_extension_counts`) or `describe_language`
itself, both of which are already fully generic (they iterate the
registry, never branch on a specific language name). This is the
"metadata/registration, not new code" extensibility RFC-07 exists to
provide for detection, the same way the generator registry already
provides it for extraction.

Deliberately layered on top of, not a replacement for,
`language_detector.py`'s existing `detect_language()`: Java+Spring-Boot
and Python detection stays exactly as it is (pom.xml/spring-boot text
match, Python manifest presence) - real, deterministic, tied to a real
`ILanguageParser`, and explicitly preserved as ADR 0018's "permanent
calibration reference." `describe_language()` only takes over once
`detect_language()` has already said neither applies - the two never
disagree on a repository `detect_language()` already recognizes.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.indexer.scanner.language_detector import DetectedLanguage, detect_language
from app.indexer.scanner.skip_directories import SKIP_DIRECTORIES


@dataclass(frozen=True)
class LanguageDescriptor:
    """Normalized detection result - what every caller (indexing
    orchestration, logging, the generic evidence generator) reasons about,
    instead of a raw `DetectedLanguage` enum value or a guessed string."""

    name: str
    extensions: tuple[str, ...]
    confidence: float
    artifact_type: str
    parser_available: bool
    deterministic_generator_available: bool
    generic_fallback_supported: bool


@dataclass(frozen=True)
class LanguageSpec:
    """One registry entry - pure data. `artifact_type` is a coarse,
    open-vocabulary label ("source", "data", "infrastructure",
    "configuration"), same open-vocabulary reasoning as
    `EvidenceItem.kind` (see `evidence.py`'s own docstring) - a new
    artifact category is a new string here, never a schema change."""

    name: str
    extensions: tuple[str, ...]
    artifact_type: str = "source"


# One entry per language/artifact type this registry recognizes for the
# *generic* (no-parser) path - Go/Rust/TypeScript/etc. below have zero
# `ILanguageParser`, so `parser_available`/`deterministic_generator_available`
# are always False for anything resolved through this table; only
# `describe_language`'s own Java/Python special case (mirroring
# `detect_language`) ever reports True for those. Extending recognition to
# a new language/extension is one more line here.
_LANGUAGE_REGISTRY: tuple[LanguageSpec, ...] = (
    LanguageSpec("go", (".go",)),
    LanguageSpec("rust", (".rs",)),
    LanguageSpec("typescript", (".ts", ".tsx")),
    LanguageSpec("javascript", (".js", ".jsx")),
    LanguageSpec("kotlin", (".kt",)),
    LanguageSpec("scala", (".scala",)),
    LanguageSpec("ruby", (".rb",)),
    LanguageSpec("php", (".php",)),
    LanguageSpec("csharp", (".cs",)),
    LanguageSpec("c", (".c", ".h")),
    LanguageSpec("cpp", (".cpp", ".hpp", ".cc")),
    LanguageSpec("dart", (".dart",)),
    LanguageSpec("lua", (".lua",)),
    LanguageSpec("perl", (".pl",)),
    LanguageSpec("r", (".r",)),
    LanguageSpec("julia", (".jl",)),
    LanguageSpec("haskell", (".hs",)),
    LanguageSpec("elixir", (".ex", ".exs")),
    LanguageSpec("swift", (".swift",)),
    LanguageSpec("sql", (".sql",), artifact_type="data"),
    LanguageSpec("terraform", (".tf", ".tfvars"), artifact_type="infrastructure"),
    LanguageSpec("yaml", (".yaml", ".yml"), artifact_type="configuration"),
    LanguageSpec("json", (".json",), artifact_type="configuration"),
    LanguageSpec("dockerfile", (".dockerfile",), artifact_type="infrastructure"),
)

_EXTENSION_TO_SPEC: dict[str, LanguageSpec] = {
    ext: spec for spec in _LANGUAGE_REGISTRY for ext in spec.extensions
}

_SCAN_FILE_CAP = 1000


def _scan_extension_counts(repo_root: Path) -> dict[str, int]:
    """One generic walk, counting every file's extension - no per-language
    branch anywhere in this loop. Bounded (`_SCAN_FILE_CAP`) so detecting
    the language of a very large repository never becomes an unbounded
    filesystem walk on its own."""
    counts: dict[str, int] = {}
    scanned = 0
    for path in repo_root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRECTORIES for part in path.parts):
            continue
        counts[path.suffix.lower()] = counts.get(path.suffix.lower(), 0) + 1
        scanned += 1
        if scanned >= _SCAN_FILE_CAP:
            break
    return counts


def describe_language(repo_root: Path) -> LanguageDescriptor:
    """The one detection entry point RFC-07's generic path uses. Falls
    through to `detect_language()`'s existing Java/Python rules first
    (unchanged, real parsers); only reaches the generic extension-count
    scan below once neither applies.

    `confidence` for a generic match is the winning language's share of
    all *recognized* extensions found (not all files - an unrecognized
    file, e.g. `LICENSE`, never dilutes it) - a repository that's
    obviously dominated by one language scores near 1.0; a genuinely mixed
    repository scores lower, information a caller could use (not acted on
    by this RFC's own callers yet, which treat any positive match as
    "generic fallback applies" - a real product surface for a future
    "flag low-confidence detections for review" feature, out of scope
    here).
    """
    detected = detect_language(repo_root)
    if detected == DetectedLanguage.JAVA_SPRING_BOOT:
        return LanguageDescriptor(
            name="java-spring-boot",
            extensions=(".java",),
            confidence=1.0,
            artifact_type="source",
            parser_available=True,
            deterministic_generator_available=True,
            generic_fallback_supported=False,
        )
    if detected == DetectedLanguage.PYTHON:
        return LanguageDescriptor(
            name="python",
            extensions=(".py",),
            confidence=1.0,
            artifact_type="source",
            parser_available=True,
            deterministic_generator_available=True,
            generic_fallback_supported=False,
        )

    extension_counts = _scan_extension_counts(repo_root)
    matched_counts: dict[str, int] = {}
    total_matched = 0
    for ext, count in extension_counts.items():
        spec = _EXTENSION_TO_SPEC.get(ext)
        if spec is None:
            continue
        matched_counts[spec.name] = matched_counts.get(spec.name, 0) + count
        total_matched += count

    if not matched_counts:
        return LanguageDescriptor(
            name="unsupported",
            extensions=(),
            confidence=0.0,
            artifact_type="unknown",
            parser_available=False,
            deterministic_generator_available=False,
            generic_fallback_supported=True,
        )

    winner_name = max(matched_counts.items(), key=lambda pair: pair[1])[0]
    winner_spec = next(spec for spec in _LANGUAGE_REGISTRY if spec.name == winner_name)
    confidence = matched_counts[winner_name] / total_matched

    return LanguageDescriptor(
        name=winner_name,
        extensions=winner_spec.extensions,
        confidence=confidence,
        artifact_type=winner_spec.artifact_type,
        parser_available=False,
        deterministic_generator_available=False,
        generic_fallback_supported=True,
    )
