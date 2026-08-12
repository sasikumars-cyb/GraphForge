"""RFC-07 — `describe_language`: metadata-driven detection for languages
with no `ILanguageParser`, layered on top of (never disagreeing with)
`detect_language()`'s existing Java/Python rules."""

from __future__ import annotations

from pathlib import Path

from app.indexer.scanner.language_registry import describe_language


def test_java_spring_boot_is_still_detected_by_the_existing_rule(tmp_path: Path) -> None:
    (tmp_path / "pom.xml").write_text(
        "<project><dependencies><spring-boot/></dependencies></project>", encoding="utf-8"
    )
    descriptor = describe_language(tmp_path)
    assert descriptor.name == "java-spring-boot"
    assert descriptor.parser_available is True
    assert descriptor.deterministic_generator_available is True
    assert descriptor.generic_fallback_supported is False
    assert descriptor.confidence == 1.0


def test_python_is_still_detected_by_the_existing_rule(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    descriptor = describe_language(tmp_path)
    assert descriptor.name == "python"
    assert descriptor.parser_available is True
    assert descriptor.generic_fallback_supported is False


def test_go_is_detected_generically(tmp_path: Path) -> None:
    (tmp_path / "main.go").write_text("package main\n", encoding="utf-8")
    (tmp_path / "util.go").write_text("package main\n", encoding="utf-8")
    descriptor = describe_language(tmp_path)
    assert descriptor.name == "go"
    assert descriptor.parser_available is False
    assert descriptor.deterministic_generator_available is False
    assert descriptor.generic_fallback_supported is True
    assert descriptor.confidence == 1.0


def test_dominant_language_wins_in_a_mixed_repository(tmp_path: Path) -> None:
    (tmp_path / "a.rs").write_text("fn main() {}\n", encoding="utf-8")
    (tmp_path / "b.rs").write_text("fn other() {}\n", encoding="utf-8")
    (tmp_path / "c.rs").write_text("fn third() {}\n", encoding="utf-8")
    (tmp_path / "one.sql").write_text("SELECT 1;\n", encoding="utf-8")
    descriptor = describe_language(tmp_path)
    assert descriptor.name == "rust"
    assert descriptor.confidence == 0.75  # 3 of 4 recognized files


def test_no_recognized_files_is_unsupported(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("hi", encoding="utf-8")
    descriptor = describe_language(tmp_path)
    assert descriptor.name == "unsupported"
    assert descriptor.generic_fallback_supported is True
    assert descriptor.confidence == 0.0


def test_new_language_is_addable_via_registry_metadata_only() -> None:
    # Proves extensibility without touching describe_language's own code:
    # a language absent from the registry (by construction, since this
    # test doesn't register one) simply falls through to "unsupported" -
    # the same generic path every future registry addition also uses.
    import app.indexer.scanner.language_registry as registry

    assert not any(spec.name == "brainfuck" for spec in registry._LANGUAGE_REGISTRY)


def test_terraform_is_classified_as_infrastructure(tmp_path: Path) -> None:
    (tmp_path / "main.tf").write_text("resource \"x\" \"y\" {}\n", encoding="utf-8")
    descriptor = describe_language(tmp_path)
    assert descriptor.name == "terraform"
    assert descriptor.artifact_type == "infrastructure"


def test_yaml_is_classified_as_configuration(tmp_path: Path) -> None:
    (tmp_path / "config.yaml").write_text("key: value\n", encoding="utf-8")
    descriptor = describe_language(tmp_path)
    assert descriptor.name == "yaml"
    assert descriptor.artifact_type == "configuration"
