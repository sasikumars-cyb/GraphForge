"""Deterministic language/framework detection — a file-presence and
substring check, nothing probabilistic.
"""

from enum import StrEnum
from pathlib import Path


class DetectedLanguage(StrEnum):
    JAVA_SPRING_BOOT = "java-spring-boot"
    UNSUPPORTED = "unsupported"


def detect_language(repo_root: Path) -> DetectedLanguage:
    """Java + Spring Boot (Maven) only, for this phase.

    Detection is intentionally narrow: a root-level `pom.xml` mentioning
    `spring-boot` anywhere in its text. Multi-module Maven projects where
    the root pom is a bare aggregator (Spring Boot only in a child module)
    are not detected — see ADR 0007.
    """
    pom_path = repo_root / "pom.xml"
    if not pom_path.is_file():
        return DetectedLanguage.UNSUPPORTED

    pom_text = pom_path.read_text(encoding="utf-8", errors="ignore")
    if "spring-boot" in pom_text:
        return DetectedLanguage.JAVA_SPRING_BOOT

    return DetectedLanguage.UNSUPPORTED
