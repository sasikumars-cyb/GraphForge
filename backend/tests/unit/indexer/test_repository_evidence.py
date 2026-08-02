"""ADR 0018 Frontier Hypothesis Generator, Finding 1 — repository-level
evidence extraction: pure filesystem reads against a real temp directory,
no mocks needed."""

from __future__ import annotations

from pathlib import Path

from app.indexer.hypotheses.repository_evidence import (
    extract_repository_evidence,
    repository_evidence_facts_to_items,
)


def test_extracts_readme_manifest_and_metadata(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# My Service\nDoes things with a database.")
    (tmp_path / "pom.xml").write_text("<project><artifactId>my-service</artifactId></project>")
    (tmp_path / "src").mkdir()

    facts = extract_repository_evidence(tmp_path)

    kinds = {f.kind for f in facts}
    assert "repository_metadata" in kinds
    assert "repository_readme" in kinds
    assert "repository_manifest" in kinds
    readme = next(f for f in facts if f.kind == "repository_readme")
    assert "database" in readme.raw_value
    metadata = next(f for f in facts if f.kind == "repository_metadata")
    assert "README.md" in metadata.raw_value
    assert "pom.xml" in metadata.raw_value


def test_never_reads_env_or_key_files(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("SECRET_API_KEY=super-secret-value")
    (tmp_path / "id_rsa").write_text("-----BEGIN PRIVATE KEY-----")
    (tmp_path / "server.pem").write_text("-----BEGIN CERTIFICATE-----")
    (tmp_path / "README.md").write_text("Fine to read.")

    facts = extract_repository_evidence(tmp_path)

    for fact in facts:
        assert "super-secret-value" not in fact.raw_value
        assert "BEGIN PRIVATE KEY" not in fact.raw_value
        assert "BEGIN CERTIFICATE" not in fact.raw_value


def test_truncates_large_files(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("x" * 10_000)

    facts = extract_repository_evidence(tmp_path)

    readme = next(f for f in facts if f.kind == "repository_readme")
    assert len(readme.raw_value) < 3_100
    assert readme.raw_value.endswith("(truncated)")


def test_missing_repository_yields_only_metadata(tmp_path: Path) -> None:
    facts = extract_repository_evidence(tmp_path)

    assert len(facts) == 1
    assert facts[0].kind == "repository_metadata"


def test_facts_to_items_are_evidence_items_with_stable_ids(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("hello")
    facts = extract_repository_evidence(tmp_path)

    items = repository_evidence_facts_to_items(
        facts, repository_id="repo-1", commit_sha="abc123", pack_id="pack:repo-1:abc123:test"
    )

    assert len(items) == len(facts)
    for item in items:
        assert item.id.startswith("evidence:repo-1:repo:")
        assert item.provenance.pack_id == "pack:repo-1:abc123:test"
        assert item.reliability_tier == 3
