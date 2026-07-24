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


def detect_language(repo_root: Path) -> DetectedLanguage:
    """Java + Spring Boot (Maven) or Python, for this phase.

    Java detection is intentionally narrow: a root-level `pom.xml` mentioning
    `spring-boot` anywhere in its text. Multi-module Maven projects where
    the root pom is a bare aggregator (Spring Boot only in a child module)
    are not detected — see ADR 0007. Python detection is presence of any
    standard manifest file at the repo root; unlike Java, no framework
    substring check — this indexer targets plain Python structure
    (packages/modules/classes/functions), not one specific framework.
    """
    pom_path = repo_root / "pom.xml"
    if pom_path.is_file():
        pom_text = pom_path.read_text(encoding="utf-8", errors="ignore")
        if "spring-boot" in pom_text:
            return DetectedLanguage.JAVA_SPRING_BOOT

    if any((repo_root / manifest).is_file() for manifest in _PYTHON_MANIFESTS):
        return DetectedLanguage.PYTHON

    return DetectedLanguage.UNSUPPORTED
