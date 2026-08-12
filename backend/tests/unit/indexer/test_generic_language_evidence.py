"""RFC-07 — `discover_generic_language_evidence`: deterministic, LLM-free
file/symbol discovery for a repository with no `ILanguageParser`."""

from __future__ import annotations

import json
from pathlib import Path

from app.indexer.hypotheses.generic_language_evidence import (
    _MAX_FILES,
    content_hash,
    discover_generic_language_evidence,
)


def test_discovers_source_files_as_deterministic_graph_node_evidence(tmp_path: Path) -> None:
    (tmp_path / "main.go").write_text('package main\nimport "fmt"\n', encoding="utf-8")
    (tmp_path / "util.go").write_text("package main\n", encoding="utf-8")

    pack = discover_generic_language_evidence(
        repository_id="repo-1", commit_sha="abc", repo_root=tmp_path, language_label="unsupported"
    )

    node_items = [i for i in pack.items if i.kind.startswith("graph_node:")]
    source_items = [i for i in pack.items if i.kind == "source_file"]
    assert len(source_items) == 2
    # Repository node + 2 SourceFile nodes.
    assert len(node_items) == 3
    assert all(item.provenance.generator.kind == "deterministic" for item in pack.items)


def test_repository_node_carries_repository_name_when_given(tmp_path: Path) -> None:
    (tmp_path / "main.go").write_text("package main\n", encoding="utf-8")
    pack = discover_generic_language_evidence(
        repository_id="repo-1",
        commit_sha="abc",
        repo_root=tmp_path,
        language_label="unsupported",
        repository_name="acme/widgets",
    )
    repo_item = next(i for i in pack.items if i.kind == "graph_node:Repository")
    assert '"name": "acme/widgets"' in repo_item.raw_value


def test_language_label_is_guessed_from_file_extensions(tmp_path: Path) -> None:
    (tmp_path / "a.go").write_text("package main\n", encoding="utf-8")
    (tmp_path / "b.go").write_text("package main\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# hi\n", encoding="utf-8")

    pack = discover_generic_language_evidence(
        repository_id="repo-1", commit_sha="abc", repo_root=tmp_path, language_label="unsupported"
    )
    repo_item = next(i for i in pack.items if i.kind == "graph_node:Repository")
    assert '"language": "go"' in repo_item.raw_value


def test_language_label_falls_back_when_no_recognized_extension(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# hi\n", encoding="utf-8")
    pack = discover_generic_language_evidence(
        repository_id="repo-1", commit_sha="abc", repo_root=tmp_path, language_label="unsupported"
    )
    repo_item = next(i for i in pack.items if i.kind == "graph_node:Repository")
    assert '"language": "unsupported"' in repo_item.raw_value


def test_language_label_prefers_the_dominant_language_in_a_mixed_repository(
    tmp_path: Path,
) -> None:
    (tmp_path / "a.go").write_text("package main\n", encoding="utf-8")
    (tmp_path / "b.go").write_text("package main\n", encoding="utf-8")
    (tmp_path / "c.rs").write_text("fn main() {}\n", encoding="utf-8")

    pack = discover_generic_language_evidence(
        repository_id="repo-1", commit_sha="abc", repo_root=tmp_path, language_label="unsupported"
    )
    repo_item = next(i for i in pack.items if i.kind == "graph_node:Repository")
    assert '"language": "go"' in repo_item.raw_value


def test_source_file_node_carries_a_content_hash(tmp_path: Path) -> None:
    (tmp_path / "main.go").write_text("package main\n", encoding="utf-8")
    pack = discover_generic_language_evidence(
        repository_id="repo-1", commit_sha="abc", repo_root=tmp_path, language_label="unsupported"
    )
    file_item = next(i for i in pack.items if i.kind == "graph_node:Component:SourceFile")
    payload = json.loads(file_item.raw_value)
    assert payload["content_hash"] == content_hash("package main\n")


def test_declaration_like_symbols_are_detected_heuristically(tmp_path: Path) -> None:
    (tmp_path / "orders.go").write_text(
        "package orders\n\nfunc Summarize() string {\n\treturn total()\n}\n\nfunc total() int {\n\treturn 0\n}\n",
        encoding="utf-8",
    )
    pack = discover_generic_language_evidence(
        repository_id="repo-1", commit_sha="abc", repo_root=tmp_path, language_label="unsupported"
    )
    symbol_items = [i for i in pack.items if i.kind == "graph_node:Component:GenericSymbol"]
    names = {json.loads(i.raw_value)["name"] for i in symbol_items}
    assert names == {"Summarize", "total"}
    assert all(i.reliability_tier == 1 for i in symbol_items)
    summarize_item = next(i for i in symbol_items if json.loads(i.raw_value)["name"] == "Summarize")
    assert summarize_item.reference.key == "repo-1:generic-symbol:orders.go:Summarize"


def test_no_declaration_like_symbols_in_a_file_with_none(tmp_path: Path) -> None:
    (tmp_path / "constants.go").write_text("package orders\n\nconst Max = 10\n", encoding="utf-8")
    pack = discover_generic_language_evidence(
        repository_id="repo-1", commit_sha="abc", repo_root=tmp_path, language_label="unsupported"
    )
    symbol_items = [i for i in pack.items if i.kind == "graph_node:Component:GenericSymbol"]
    assert symbol_items == []


def test_binary_and_lock_files_are_skipped(tmp_path: Path) -> None:
    (tmp_path / "photo.png").write_bytes(b"\x89PNG\r\n")
    (tmp_path / "yarn.lock").write_text("lockfile", encoding="utf-8")
    (tmp_path / "main.go").write_text("package main\n", encoding="utf-8")

    pack = discover_generic_language_evidence(
        repository_id="repo-1", commit_sha="abc", repo_root=tmp_path, language_label="unsupported"
    )
    source_items = [i for i in pack.items if i.kind == "source_file"]
    assert len(source_items) == 1
    assert source_items[0].reference.locator == "main.go"


def test_file_count_is_capped(tmp_path: Path) -> None:
    for i in range(_MAX_FILES + 10):
        (tmp_path / f"f{i}.go").write_text("package main\n", encoding="utf-8")

    pack = discover_generic_language_evidence(
        repository_id="repo-1", commit_sha="abc", repo_root=tmp_path, language_label="unsupported"
    )
    source_items = [i for i in pack.items if i.kind == "source_file"]
    assert len(source_items) == _MAX_FILES


def test_node_ids_match_the_repository_wide_namespaced_scheme(tmp_path: Path) -> None:
    (tmp_path / "main.go").write_text("package main\n", encoding="utf-8")
    pack = discover_generic_language_evidence(
        repository_id="repo-1", commit_sha="abc", repo_root=tmp_path, language_label="unsupported"
    )
    file_item = next(i for i in pack.items if i.kind.startswith("graph_node:Component"))
    assert file_item.reference.key == "repo-1:source-file:main.go"


def test_no_source_files_still_produces_a_repository_node(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("hi", encoding="utf-8")
    pack = discover_generic_language_evidence(
        repository_id="repo-1", commit_sha="abc", repo_root=tmp_path, language_label="unsupported"
    )
    assert any(i.kind == "graph_node:Repository" for i in pack.items)
