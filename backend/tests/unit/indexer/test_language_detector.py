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


def test_databricks_bundle_at_root_with_python_source_is_python(tmp_path: Path) -> None:
    (tmp_path / "databricks.yml").write_text("bundle:\n  name: example\n", encoding="utf-8")
    (tmp_path / "ingest.py").write_text("def run():\n    pass\n", encoding="utf-8")
    assert detect_language(tmp_path) == DetectedLanguage.PYTHON


def test_databricks_bundle_in_subdirectory_with_python_source_is_python(tmp_path: Path) -> None:
    # The layout actually observed in the wild: `databricks/databricks.yml`,
    # not a root-level manifest.
    bundle_dir = tmp_path / "databricks"
    bundle_dir.mkdir()
    (bundle_dir / "databricks.yml").write_text("bundle:\n  name: example\n", encoding="utf-8")
    (tmp_path / "ingest_pipelines" / "job.py").parent.mkdir(parents=True)
    (tmp_path / "ingest_pipelines" / "job.py").write_text("def run():\n    pass\n", encoding="utf-8")
    assert detect_language(tmp_path) == DetectedLanguage.PYTHON


def test_databricks_bundle_without_python_source_stays_unsupported(tmp_path: Path) -> None:
    # A bundle can just as validly wrap a Scala spark_jar_task or a SQL/dbt
    # task — with no .py anywhere in the tree, this must not be guessed as
    # Python (there's no parser for Scala/SQL, so PythonParser would
    # silently produce an empty/wrong graph instead of an honest failure).
    (tmp_path / "databricks.yml").write_text("bundle:\n  name: example\n", encoding="utf-8")
    (tmp_path / "job.scala").write_text("object Job extends App {}\n", encoding="utf-8")
    assert detect_language(tmp_path) == DetectedLanguage.UNSUPPORTED


def test_databricks_bundle_deeper_than_one_level_is_not_found(tmp_path: Path) -> None:
    # Matches the pom.xml precedent: root-level (or one level below) only,
    # not a full recursive repo walk.
    nested = tmp_path / "deploy" / "config"
    nested.mkdir(parents=True)
    (nested / "databricks.yml").write_text("bundle:\n  name: example\n", encoding="utf-8")
    (tmp_path / "ingest.py").write_text("def run():\n    pass\n", encoding="utf-8")
    assert detect_language(tmp_path) == DetectedLanguage.UNSUPPORTED
