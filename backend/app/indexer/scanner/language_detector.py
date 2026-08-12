"""Deterministic language/framework detection — a file-presence and
substring check, nothing probabilistic.
"""

from enum import StrEnum
from pathlib import Path


class DetectedLanguage(StrEnum):
    JAVA_SPRING_BOOT = "java-spring-boot"
    PYTHON = "python"
    UNSUPPORTED = "unsupported"


_PYTHON_MANIFESTS = ("pyproject.toml", "requirements.txt", "setup.py", "Pipfile")

# A Databricks Asset Bundle manifest — `databricks bundle init` scaffolds it
# either at the repo root or inside a top-level subdirectory (observed:
# `databricks/databricks.yml`), never deeper. Checked at both locations,
# same "root-level, not a full repo walk" precedent the pom.xml check above
# already sets - not a recursive search.
_DATABRICKS_BUNDLE_MANIFEST = "databricks.yml"


def _find_databricks_bundle_manifest(repo_root: Path) -> Path | None:
    if (root_manifest := repo_root / _DATABRICKS_BUNDLE_MANIFEST).is_file():
        return root_manifest
    return next(
        (path for path in repo_root.glob(f"*/{_DATABRICKS_BUNDLE_MANIFEST}") if path.is_file()),
        None,
    )


def _bundle_wraps_python(repo_root: Path) -> bool:
    """A Databricks Asset Bundle manifest says nothing about language on its
    own — bundle.yml/databricks.yml is a deployment descriptor (targets,
    workspace paths, `include: resources/*.yml` job definitions), and a
    bundle can just as validly wrap a `spark_jar_task` (Scala), `sql_task`/
    `dbt_task` (SQL), or a multi-language notebook as it can Python. Only
    classify the repository as PYTHON when real `.py` source is actually
    present in the tree; a Scala/SQL/JAR-only bundle must keep detecting as
    UNSUPPORTED rather than being handed to `PythonParser`, which has no way
    to represent non-Python source and would silently produce an empty or
    misleading graph instead of the honest "not supported yet" this
    detector otherwise guarantees.
    """
    return next(repo_root.rglob("*.py"), None) is not None


def detect_language(repo_root: Path) -> DetectedLanguage:
    """Java + Spring Boot (Maven) or Python, for this phase.

    Java detection is intentionally narrow: a root-level `pom.xml` mentioning
    `spring-boot` anywhere in its text. Multi-module Maven projects where
    the root pom is a bare aggregator (Spring Boot only in a child module)
    are not detected — see ADR 0007. Python detection is presence of any
    standard manifest file at the repo root; unlike Java, no framework
    substring check — this indexer targets plain Python structure
    (packages/modules/classes/functions), not one specific framework.

    A third, narrower path exists specifically for Databricks Asset Bundle
    projects that carry no standard Python manifest at all (their dependency/
    deploy config lives in `databricks.yml` instead - see
    `_bundle_wraps_python`'s docstring for why that file alone is not
    treated as a language signal). This is a generic, file-presence-based
    addition to the same deterministic scheme above, not a repository- or
    organization-specific special case: any repository anywhere using a
    Databricks Asset Bundle with real Python source benefits, and one whose
    bundle wraps Scala/SQL/JAR tasks correctly keeps detecting as
    UNSUPPORTED (this codebase has no parser for those languages yet).
    """
    pom_path = repo_root / "pom.xml"
    if pom_path.is_file():
        pom_text = pom_path.read_text(encoding="utf-8", errors="ignore")
        if "spring-boot" in pom_text:
            return DetectedLanguage.JAVA_SPRING_BOOT

    if any((repo_root / manifest).is_file() for manifest in _PYTHON_MANIFESTS):
        return DetectedLanguage.PYTHON

    if _find_databricks_bundle_manifest(repo_root) is not None and _bundle_wraps_python(
        repo_root
    ):
        return DetectedLanguage.PYTHON

    return DetectedLanguage.UNSUPPORTED
