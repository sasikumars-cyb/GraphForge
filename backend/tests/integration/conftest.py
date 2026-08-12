"""Fixtures shared by the indexer integration tests: real, git-init'd local
repositories used as clone sources (see `app.indexer.scanner.repository_cloner`
- it shells out to a real `git clone`, so these need to be real repos, not
mocked file lists).
"""

import shutil
import subprocess
from pathlib import Path

import pytest

FIXTURE_ROOT = Path(__file__).parent.parent / "fixtures" / "spring_boot_sample"
PYTHON_FIXTURE_ROOT = Path(__file__).parent.parent / "fixtures" / "python_sample"
PYTHON_SPARK_FIXTURE_ROOT = Path(__file__).parent.parent / "fixtures" / "python_spark_sample"
GO_FIXTURE_ROOT = Path(__file__).parent.parent / "fixtures" / "go_sample"

_GIT_AUTHOR = ["-c", "user.email=test@example.com", "-c", "user.name=Test"]


def _init_git_repo(repo_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo_path, check=True)
    subprocess.run(["git", *_GIT_AUTHOR, "add", "-A"], cwd=repo_path, check=True)
    subprocess.run(
        ["git", *_GIT_AUTHOR, "commit", "-q", "-m", "initial"], cwd=repo_path, check=True
    )
    subprocess.run(["git", "branch", "-m", "main"], cwd=repo_path, check=True)


@pytest.fixture
def spring_boot_git_repo(tmp_path: Path) -> Path:
    """A real local git repository whose working tree is a copy of the
    `spring_boot_sample` fixture - a genuine clone source for
    `clone_repository`/`index_repository`, not a mock."""
    repo_path = tmp_path / "spring-boot-repo"
    shutil.copytree(FIXTURE_ROOT, repo_path)
    _init_git_repo(repo_path)
    return repo_path


@pytest.fixture
def python_git_repo(tmp_path: Path) -> Path:
    """A real local git repository whose working tree is a copy of the
    `python_sample` fixture."""
    repo_path = tmp_path / "python-repo"
    shutil.copytree(PYTHON_FIXTURE_ROOT, repo_path)
    _init_git_repo(repo_path)
    return repo_path


@pytest.fixture
def python_spark_git_repo(tmp_path: Path) -> Path:
    """A real local git repository whose working tree is a copy of the
    `python_spark_sample` fixture - a small Spark/Databricks-shaped
    repository (a `spark.sql(...)` INSERT...SELECT, a standalone `.sql`
    file, and a literal filename registry) used only by the SQL-lineage
    end-to-end integration test. Deliberately separate from
    `python_git_repo`'s `python_sample` fixture so existing tests that
    assert exact counts against that fixture are untouched."""
    repo_path = tmp_path / "python-spark-repo"
    shutil.copytree(PYTHON_SPARK_FIXTURE_ROOT, repo_path)
    _init_git_repo(repo_path)
    return repo_path


@pytest.fixture
def go_git_repo(tmp_path: Path) -> Path:
    """A real local git repository whose working tree is a copy of the
    `go_sample` fixture - RFC-07's proof language: GraphForge has no
    `ILanguageParser`, and no `detect_language()` rule, for Go at all.
    Used only by the generic-language-fallback integration test."""
    repo_path = tmp_path / "go-repo"
    shutil.copytree(GO_FIXTURE_ROOT, repo_path)
    _init_git_repo(repo_path)
    return repo_path


@pytest.fixture
def unsupported_git_repo(tmp_path: Path) -> Path:
    """A real local git repository with a `pom.xml` that doesn't mention
    Spring Boot - exercises the "unsupported repository" path."""
    repo_path = tmp_path / "unsupported-repo"
    repo_path.mkdir()
    (repo_path / "pom.xml").write_text(
        "<project><groupId>com.example</groupId><artifactId>plain</artifactId></project>",
        encoding="utf-8",
    )
    _init_git_repo(repo_path)
    return repo_path
