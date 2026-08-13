"""RFC-0019 — Repo-wide `.yml`/`.yaml`/`.json` deployment/config file
discovery: generic key/value flattening, never a schema-specific parser
(not a Databricks-bundle model, not a Kubernetes-manifest model, not a
CI-config model) — just "this file has structured key/value data, flatten
it into searchable text," the same "treat it as a text blob, don't model
its domain" discipline `hypotheses/repository_evidence.py` already uses
for README/manifest content, applied here to files that live anywhere in
the tree rather than a fixed top-level allowlist.

Deliberately not part of any `ILanguageParser`, same reasoning as
`sql_file_extractor.py`: config/deployment files commonly sit alongside
Python (or Java) source rather than being "the" detected language of a
repository, so this runs unconditionally after language-specific parsing
(see `indexer/services/indexing_service.py`).

No repository-, key-, or value-specific logic anywhere here. A key named
`opco`/`tenant`/`env`/`region` (or anything else) is never named or
special-cased — every key found while flattening is treated identically,
which is what lets this same extractor surface an operational identifier
for *any* ticket, in *any* repository, without per-domain tuning.

Safety: filenames shaped like committed secrets are never read, mirroring
`repository_evidence.py`'s own `_is_safe_to_read` guard exactly (kept as
a second, independent copy rather than a shared import — this module and
that one serve different pipelines and neither should have to import the
other's internals to stay safe).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from app.indexer.models.architecture import ConfigFile, ConfigPathReference, SourceLocation
from app.indexer.scanner.skip_directories import SKIP_DIRECTORIES

_CONFIG_EXTENSIONS = (".yml", ".yaml", ".json")

_NEVER_READ_BASENAME_PREFIXES = (".env", "id_rsa", "credentials", "secret")
_NEVER_READ_SUFFIXES = (".pem", ".key", ".pfx", ".p12")

# Conservative bound — a config file's flattened text is evidence, not a
# faithful re-serialization of the whole file: bounded exactly like
# `MAX_SOURCE_FILES_PER_CANDIDATE`/`_component_budget` elsewhere in this
# codebase are bounded, so one unusually large manifest can't dominate a
# repository's evidence or blow up prompt/context size downstream.
#
# RFC-0020 — `_MAX_PAIRS` (a count of flattened key/value pairs) is the
# *only* bound now; an earlier character-count truncation on top of it
# was removed. That second bound truncated the *joined* text in raw
# dict-insertion order, which silently discarded whichever fields
# happened to appear later in a file's own structure — for a real config
# (permissions/notifications declared before parameters/tasks, say), that
# reliably cut off exactly the fields most likely to matter, regardless
# of this extractor's own care not to hardcode which keys matter. Pairs
# are already a generic, self-limiting unit — 200 of them is a real bound
# on evidence size without being sensitive to where in the file the
# meaningful ones happen to live.
_MAX_PAIRS = 200
_MAX_DEPTH = 8

# A scalar value is treated as a candidate reference to another file when
# it contains a path separator and ends in one of these common source-file
# extensions — a shape check, not a specific filename or path anywhere.
_REFERENCE_EXTENSIONS = (".py", ".java", ".sql", ".scala", ".sh", ".js", ".ts", ".ipynb")


def _is_safe_to_read(relative_path: str) -> bool:
    basename = Path(relative_path).name.lower()
    if basename.startswith(_NEVER_READ_BASENAME_PREFIXES):
        return False
    return not basename.endswith(_NEVER_READ_SUFFIXES)


def _iter_config_files(repo_root: Path) -> list[Path]:
    return [
        path
        for ext in _CONFIG_EXTENSIONS
        for path in repo_root.rglob(f"*{ext}")
        if not any(part in SKIP_DIRECTORIES for part in path.parts)
        and _is_safe_to_read(str(path.relative_to(repo_root)))
    ]


def _flatten(value: Any, prefix: str = "", depth: int = 0) -> list[tuple[str, str]]:
    """Nested dict/list structure -> flat `(dotted_key, scalar_value)`
    pairs, depth- and count-bounded. A key with an empty/`None` value
    contributes nothing — it carries no identifier worth matching."""
    if depth > _MAX_DEPTH:
        return []
    pairs: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for k, v in value.items():
            key = f"{prefix}.{k}" if prefix else str(k)
            pairs.extend(_flatten(v, key, depth + 1))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            key = f"{prefix}[{i}]" if prefix else f"[{i}]"
            pairs.extend(_flatten(v, key, depth + 1))
    elif value is not None and value != "":
        pairs.append((prefix, str(value)))
    return pairs[:_MAX_PAIRS]


def _looks_like_file_reference(value: str) -> bool:
    if "/" not in value or any(c.isspace() for c in value):
        return False
    return value.split("?", 1)[0].rstrip("/").lower().endswith(_REFERENCE_EXTENSIONS)


def _parse_structured(text: str, suffix: str) -> Any | None:
    try:
        if suffix == ".json":
            return json.loads(text)
        return yaml.safe_load(text)
    except Exception:
        # Malformed/templated YAML (custom tags, environment-substitution
        # syntax a generic parser can't resolve) — skip, don't guess.
        return None


def extract_config_files(repo_root: Path) -> tuple[list[ConfigFile], list[ConfigPathReference]]:
    """Every `.yml`/`.yaml`/`.json` file in the repository, flattened into
    one `ConfigFile` (searchable, bounded text) each, plus every scalar
    value found along the way that has the shape of a reference to
    another file (`ConfigPathReference` — resolved against this
    repository's own already-discovered files in `graph/builder.py`, the
    same in-repo-only, ambiguity-safe pattern `PythonSqlFileReference`
    resolution already uses).
    """
    config_files: list[ConfigFile] = []
    references: list[ConfigPathReference] = []

    for path in _iter_config_files(repo_root):
        relative_path = str(path.relative_to(repo_root))
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        data = _parse_structured(text, path.suffix.lower())
        if data is None:
            continue

        pairs = _flatten(data)
        if not pairs:
            continue

        flattened_text = " ".join(f"{k} {v}" for k, v in pairs)
        location = SourceLocation(file_path=relative_path)
        config_files.append(
            ConfigFile(name=path.stem, location=location, flattened_text=flattened_text)
        )

        for key, value in pairs:
            if _looks_like_file_reference(value):
                references.append(
                    ConfigPathReference(
                        config_file=relative_path,
                        key=key,
                        referenced_text=value,
                        location=location,
                    )
                )

    return config_files, references
