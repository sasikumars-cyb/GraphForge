"""Language/framework detection: file presence + substring check only."""

from pathlib import Path

from app.indexer.scanner.language_detector import DetectedLanguage, detect_language

FIXTURE_ROOT = Path(__file__).parent.parent.parent / "fixtures" / "spring_boot_sample"
PYTHON_FIXTURE_ROOT = Path(__file__).parent.parent.parent / "fixtures" / "python_sample"


def test_detects_spring_boot_maven_project() -> None:
    assert detect_language(FIXTURE_ROOT) == DetectedLanguage.JAVA_SPRING_BOOT


def test_missing_pom_is_unsupported(tmp_path: Path) -> None:
    assert detect_language(tmp_path) == DetectedLanguage.UNSUPPORTED


def test_pom_without_spring_boot_is_unsupported(tmp_path: Path) -> None:
    (tmp_path / "pom.xml").write_text(
        "<project><groupId>com.example</groupId></project>", encoding="utf-8"
    )
    assert detect_language(tmp_path) == DetectedLanguage.UNSUPPORTED


def test_detects_python_project_via_pyproject_toml() -> None:
    assert detect_language(PYTHON_FIXTURE_ROOT) == DetectedLanguage.PYTHON


def test_detects_python_project_via_requirements_txt(tmp_path: Path) -> None:
    (tmp_path / "requirements.txt").write_text("fastapi>=0.100\n", encoding="utf-8")
    assert detect_language(tmp_path) == DetectedLanguage.PYTHON


def test_detects_python_project_via_setup_py(tmp_path: Path) -> None:
    (tmp_path / "setup.py").write_text("from setuptools import setup\n", encoding="utf-8")
    assert detect_language(tmp_path) == DetectedLanguage.PYTHON


def test_java_pom_takes_precedence_over_python_manifests(tmp_path: Path) -> None:
    (tmp_path / "pom.xml").write_text(
        "<project><dependencies><spring-boot/></dependencies></project>", encoding="utf-8"
    )
    (tmp_path / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
    assert detect_language(tmp_path) == DetectedLanguage.JAVA_SPRING_BOOT
