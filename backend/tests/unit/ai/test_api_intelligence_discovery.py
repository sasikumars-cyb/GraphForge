"""Unit tests for app.agents.api_intelligence.discovery — pure filesystem
relationship discovery, no DB/network/LLM."""

from __future__ import annotations

from pathlib import Path

from app.agents.api_intelligence.discovery import discover_relationships
from app.agents.documentation.discovery import discover_markdown_files


def test_discovers_a_real_internal_link(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("See [the API docs](api.md) for details.")
    (tmp_path / "api.md").write_text("# API\n")

    files = discover_markdown_files(tmp_path)
    edges = discover_relationships(tmp_path, files)

    assert len(edges) == 1
    assert edges[0].from_file == "README.md"
    assert edges[0].to_file == "api.md"


def test_ignores_external_links(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("See [external](https://example.com/docs).")

    files = discover_markdown_files(tmp_path)
    edges = discover_relationships(tmp_path, files)

    assert edges == []


def test_ignores_links_to_non_markdown_files(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("See [the schema](schema.json).")
    (tmp_path / "schema.json").write_text("{}")

    files = discover_markdown_files(tmp_path)
    edges = discover_relationships(tmp_path, files)

    assert edges == []


def test_deduplicates_repeated_links_between_the_same_pair(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("[a](api.md) and again [b](api.md)")
    (tmp_path / "api.md").write_text("# API\n")

    files = discover_markdown_files(tmp_path)
    edges = discover_relationships(tmp_path, files)

    assert len(edges) == 1


def test_ignores_self_links_and_anchors(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("[jump](#section) and [self](README.md#section)")

    files = discover_markdown_files(tmp_path)
    edges = discover_relationships(tmp_path, files)

    assert edges == []
