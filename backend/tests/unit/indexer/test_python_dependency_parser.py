"""Unit tests for `parse_python_dependencies` - PEP 621, Poetry, and
`requirements.txt` manifests."""

from pathlib import Path

from app.indexer.parsers.python.dependency_parser import parse_python_dependencies


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
