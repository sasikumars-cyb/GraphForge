"""Unit tests for `parse_python_dependencies` - PEP 621, Poetry, and
`requirements.txt` manifests."""

from pathlib import Path

from app.indexer.parsers.python.dependency_parser import (
    parse_python_dependencies,
    parse_python_package_name,
)


def test_pep621_dependencies(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\ndependencies = ["fastapi>=0.100", "requests"]\n',
        encoding="utf-8",
    )
    dependencies = {d.name: d.version for d in parse_python_dependencies(tmp_path)}
    assert dependencies == {"fastapi": ">=0.100", "requests": None}


def test_poetry_dependencies_exclude_the_python_interpreter_constraint(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[tool.poetry.dependencies]\npython = "^3.12"\nfastapi = "^0.100"\n'
        'requests = { version = "2.31.0" }\n',
        encoding="utf-8",
    )
    dependencies = {d.name: d.version for d in parse_python_dependencies(tmp_path)}
    assert dependencies == {"fastapi": "^0.100", "requests": "2.31.0"}


def test_requirements_txt_fallback_when_no_pyproject(tmp_path: Path) -> None:
    (tmp_path / "requirements.txt").write_text(
        "# a comment\nfastapi==0.100.0\nrequests\n-e ./local-pkg\n\n",
        encoding="utf-8",
    )
    dependencies = {d.name: d.version for d in parse_python_dependencies(tmp_path)}
    assert dependencies == {"fastapi": "==0.100.0", "requests": None}


def test_no_manifest_returns_empty_list(tmp_path: Path) -> None:
    assert parse_python_dependencies(tmp_path) == []


def test_malformed_toml_falls_back_to_requirements_txt(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("not valid toml [[[", encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("requests==2.31.0\n", encoding="utf-8")
    dependencies = {d.name: d.version for d in parse_python_dependencies(tmp_path)}
    assert dependencies == {"requests": "==2.31.0"}


# ---------------------------------------------------------------------------
# RFC-0012 — `parse_python_package_name`: the repository's own self-
# declared identity, distinct from its git repository name.
# ---------------------------------------------------------------------------


def test_package_name_from_pep621_project_table(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "shared_jobs"\ndependencies = []\n', encoding="utf-8"
    )
    assert parse_python_package_name(tmp_path) == "shared_jobs"


def test_package_name_from_poetry_table_differs_from_repo_name_by_design(tmp_path: Path) -> None:
    """The real-world case this exists for: a repository named
    `up-databricks-shared-jobs` publishing a package named `shared_jobs` —
    two different identities, and only this one is what a source-level
    `import shared_jobs` elsewhere actually names."""
    (tmp_path / "pyproject.toml").write_text(
        '[tool.poetry]\nname = "shared_jobs"\nversion = "1.0.0"\n', encoding="utf-8"
    )
    assert parse_python_package_name(tmp_path) == "shared_jobs"


def test_package_name_none_when_no_pyproject(tmp_path: Path) -> None:
    assert parse_python_package_name(tmp_path) is None


def test_package_name_none_when_manifest_declares_no_name(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[tool.poetry.dependencies]\npython = "^3.12"\n', encoding="utf-8"
    )
    assert parse_python_package_name(tmp_path) is None
