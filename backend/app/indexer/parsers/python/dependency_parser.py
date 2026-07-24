"""Parses `pyproject.toml` (PEP 621 `[project.dependencies]` or Poetry's
`[tool.poetry.dependencies]`) and `requirements.txt` for direct Python
dependencies - the `pom_parser.py` equivalent. Deliberately does not
resolve lockfiles, extras, or environment markers: records exactly what
the manifest declares (see ADR 0007 precedent for Maven)."""

import re
import tomllib
from pathlib import Path
from typing import Any

from app.indexer.models.architecture import PythonDependency

_VERSION_SPLIT = re.compile(r"[<>=!~\[; ]")


def _name_and_version_from_requirement(requirement: str) -> tuple[str, str | None] | None:
    """`requests==2.31.0` -> ("requests", "==2.31.0"); `requests` -> ("requests", None)."""
    requirement = requirement.strip()
    if not requirement or requirement.startswith(("#", "-")):
        return None
    match = _VERSION_SPLIT.search(requirement)
    if match is None:
        return (requirement, None)
    name = requirement[: match.start()].strip()
    version = requirement[match.start() :].strip() or None
    return (name, version) if name else None


def _parse_requirements_txt(path: Path) -> list[PythonDependency]:
    dependencies: list[PythonDependency] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        parsed = _name_and_version_from_requirement(line)
        if parsed is not None:
            dependencies.append(PythonDependency(name=parsed[0], version=parsed[1]))
    return dependencies


def _parse_pep621_dependencies(project_table: dict[str, Any]) -> list[PythonDependency]:
    dependencies: list[PythonDependency] = []
    for requirement in project_table.get("dependencies", []):
        parsed = _name_and_version_from_requirement(requirement)
        if parsed is not None:
            dependencies.append(PythonDependency(name=parsed[0], version=parsed[1]))
    return dependencies


def _parse_poetry_dependencies(poetry_table: dict[str, Any]) -> list[PythonDependency]:
    dependencies: list[PythonDependency] = []
    for name, spec in poetry_table.get("dependencies", {}).items():
        if name == "python":
            continue
        version = (
            spec
            if isinstance(spec, str)
            else spec.get("version") if isinstance(spec, dict) else None
        )
        dependencies.append(PythonDependency(name=name, version=version))
    return dependencies


def _parse_pyproject_toml(path: Path) -> list[PythonDependency]:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except tomllib.TOMLDecodeError:
        return []

    project_deps = _parse_pep621_dependencies(data.get("project", {}))
    if project_deps:
        return project_deps

    poetry_table = data.get("tool", {}).get("poetry", {})
    return _parse_poetry_dependencies(poetry_table) if poetry_table else []


def parse_python_dependencies(repo_root: Path) -> list[PythonDependency]:
    pyproject_path = repo_root / "pyproject.toml"
    if pyproject_path.is_file():
        dependencies = _parse_pyproject_toml(pyproject_path)
        if dependencies:
            return dependencies

    requirements_path = repo_root / "requirements.txt"
    if requirements_path.is_file():
        return _parse_requirements_txt(requirements_path)

    return []
