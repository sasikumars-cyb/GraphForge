"""Parses `pom.xml` for direct Maven dependencies.

Deliberately does not resolve parent POMs, BOMs, or property placeholders
like `${spring.version}` — records exactly what this one file declares. See
ADR 0007. `<dependencyManagement>` is intentionally excluded: those are
version *constraints*, not dependencies actually used.
"""

import xml.etree.ElementTree as ElementTree
from pathlib import Path

from app.indexer.models.architecture import MavenDependency


def _namespace_of(tag: str) -> str | None:
    return tag[1:].split("}")[0] if tag.startswith("{") else None


def _qualify(tag: str, namespace: str | None) -> str:
    return f"{{{namespace}}}{tag}" if namespace else tag


def _find_text(parent: ElementTree.Element, tag: str, namespace: str | None) -> str | None:
    element = parent.find(_qualify(tag, namespace))
    return element.text.strip() if element is not None and element.text else None


def parse_maven_dependencies(pom_path: Path) -> list[MavenDependency]:
    try:
        root = ElementTree.parse(pom_path).getroot()
    except ElementTree.ParseError:
        return []

    namespace = _namespace_of(root.tag)

    # A direct child of <project> only - so <dependencyManagement>'s own
    # nested <dependencies> (a different parent element) is never matched.
    dependencies_element = root.find(_qualify("dependencies", namespace))
    if dependencies_element is None:
        return []

    dependencies: list[MavenDependency] = []
    for dependency in dependencies_element.findall(_qualify("dependency", namespace)):
        group_id = _find_text(dependency, "groupId", namespace)
        artifact_id = _find_text(dependency, "artifactId", namespace)
        if not group_id or not artifact_id:
            continue
        dependencies.append(
            MavenDependency(
                group_id=group_id,
                artifact_id=artifact_id,
                version=_find_text(dependency, "version", namespace),
                scope=_find_text(dependency, "scope", namespace),
            )
        )

    return dependencies
