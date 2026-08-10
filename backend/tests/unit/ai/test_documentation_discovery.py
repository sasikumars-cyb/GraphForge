"""Unit tests for app.agents.documentation.discovery — pure filesystem
functions, no DB/network, tested against plain temp directories."""

from __future__ import annotations

from pathlib import Path

from app.agents.documentation.discovery import (
    discover_markdown_files,
    find_broken_links,
    find_duplicate_documents,
)


def _write(root: Path, relative: str, content: str = "content") -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def test_discovers_readme_docs_and_adr_files(tmp_path: Path) -> None:
    _write(tmp_path, "README.md")
    _write(tmp_path, "docs/architecture.md")
    _write(tmp_path, "ADR/0001-use-postgres.md")
    _write(tmp_path, "notes.md")

    files = discover_markdown_files(tmp_path)
    by_path = {f.relative_path: f for f in files}

    assert set(by_path) == {
        "README.md",
        "docs/architecture.md",
        "ADR/0001-use-postgres.md",
        "notes.md",
    }
    assert by_path["README.md"].category == "readme"
    assert by_path["docs/architecture.md"].category == "docs"
    assert by_path["ADR/0001-use-postgres.md"].category == "adr"
    assert by_path["notes.md"].category == "other"


def test_skips_vendor_and_build_directories(tmp_path: Path) -> None:
    _write(tmp_path, "README.md")
    _write(tmp_path, "node_modules/some-pkg/README.md")
    _write(tmp_path, "vendor/lib/README.md")
    _write(tmp_path, ".git/COMMIT_EDITMSG.md")

    files = discover_markdown_files(tmp_path)

    assert [f.relative_path for f in files] == ["README.md"]


def test_ignores_non_markdown_files(tmp_path: Path) -> None:
    _write(tmp_path, "README.md")
    _write(tmp_path, "notes.txt")

    files = discover_markdown_files(tmp_path)

    assert [f.relative_path for f in files] == ["README.md"]


def test_finds_a_broken_internal_link(tmp_path: Path) -> None:
    _write(tmp_path, "README.md", "See [architecture](docs/architecture.md) for details.")
    files = discover_markdown_files(tmp_path)

    broken = find_broken_links(tmp_path, files)

    assert len(broken) == 1
    assert broken[0].source_file == "README.md"
    assert broken[0].target == "docs/architecture.md"


def test_does_not_flag_a_link_that_resolves(tmp_path: Path) -> None:
    _write(tmp_path, "README.md", "See [architecture](docs/architecture.md) for details.")
    _write(tmp_path, "docs/architecture.md", "Architecture doc.")
    files = discover_markdown_files(tmp_path)

    broken = find_broken_links(tmp_path, files)

    assert broken == []


def test_ignores_external_links_and_anchors(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "README.md",
        "See [external](https://example.com/docs) and [section](#usage) and "
        "[mail](mailto:a@b.com).",
    )
    files = discover_markdown_files(tmp_path)

    broken = find_broken_links(tmp_path, files)

    assert broken == []


def test_does_not_flag_a_link_escaping_the_repository(tmp_path: Path) -> None:
    _write(tmp_path, "README.md", "See [outside](../../../etc/passwd) for details.")
    files = discover_markdown_files(tmp_path)

    broken = find_broken_links(tmp_path, files)

    assert broken == []


def test_finds_exact_duplicate_documents(tmp_path: Path) -> None:
    _write(tmp_path, "docs/a.md", "Shared   content\nacross files.")
    _write(
        tmp_path, "docs/b.md", "Shared content across files."
    )  # same after whitespace normalization
    _write(tmp_path, "docs/c.md", "Completely different.")
    files = discover_markdown_files(tmp_path)

    pairs = find_duplicate_documents(files)

    assert len(pairs) == 1
    original, duplicate = pairs[0]
    assert original.relative_path == "docs/a.md"
    assert duplicate.relative_path == "docs/b.md"


def test_no_duplicates_when_content_differs(tmp_path: Path) -> None:
    _write(tmp_path, "docs/a.md", "One thing.")
    _write(tmp_path, "docs/b.md", "A different thing.")
    files = discover_markdown_files(tmp_path)

    assert find_duplicate_documents(files) == []
