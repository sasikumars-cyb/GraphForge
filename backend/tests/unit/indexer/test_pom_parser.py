"""`pom.xml` -> direct Maven dependencies, excluding dependencyManagement."""

from pathlib import Path

from app.indexer.parsers.java.pom_parser import parse_maven_dependencies

FIXTURE_POM = Path(__file__).parent.parent.parent / "fixtures" / "spring_boot_sample" / "pom.xml"


def test_parses_direct_dependencies() -> None:
    dependencies = parse_maven_dependencies(FIXTURE_POM)

    by_artifact = {dep.artifact_id: dep for dep in dependencies}
    assert set(by_artifact) == {
        "spring-boot-starter-web",
        "spring-cloud-starter-openfeign",
        "spring-kafka",
        "spring-boot-starter-test",
    }


def test_dependency_management_entries_are_excluded() -> None:
    dependencies = parse_maven_dependencies(FIXTURE_POM)

    assert "spring-cloud-dependencies" not in {dep.artifact_id for dep in dependencies}


def test_captures_version_and_scope() -> None:
    dependencies = parse_maven_dependencies(FIXTURE_POM)

    feign = next(dep for dep in dependencies if dep.artifact_id == "spring-cloud-starter-openfeign")
    assert feign.group_id == "org.springframework.cloud"
    assert feign.version == "4.1.0"

    test_dep = next(dep for dep in dependencies if dep.artifact_id == "spring-boot-starter-test")
    assert test_dep.scope == "test"


def test_dependency_without_direct_dependencies_element_returns_empty(tmp_path: Path) -> None:
    pom = tmp_path / "pom.xml"
    pom.write_text("<project></project>", encoding="utf-8")

    assert parse_maven_dependencies(pom) == []


def test_malformed_xml_returns_empty(tmp_path: Path) -> None:
    pom = tmp_path / "pom.xml"
    pom.write_text("<project><unclosed>", encoding="utf-8")

    assert parse_maven_dependencies(pom) == []
