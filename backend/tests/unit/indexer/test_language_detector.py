"""Language/framework detection: file presence + substring check only."""

from pathlib import Path

from app.indexer.scanner.language_detector import DetectedLanguage, detect_language

FIXTURE_ROOT = Path(__file__).parent.parent.parent / "fixtures" / "spring_boot_sample"


def test_detects_spring_boot_maven_project() -> None:
    assert detect_language(FIXTURE_ROOT) == DetectedLanguage.JAVA_SPRING_BOOT


def test_missing_pom_is_unsupported(tmp_path: Path) -> None:
    assert detect_language(tmp_path) == DetectedLanguage.UNSUPPORTED


def test_pom_without_spring_boot_is_unsupported(tmp_path: Path) -> None:
    (tmp_path / "pom.xml").write_text(
        "<project><groupId>com.example</groupId></project>", encoding="utf-8"
    )
    assert detect_language(tmp_path) == DetectedLanguage.UNSUPPORTED
