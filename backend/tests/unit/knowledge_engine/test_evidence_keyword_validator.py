"""ADR 0018 — `EvidenceKeywordValidator` and its four registered instances."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.knowledge_engine.contracts.evidence import (
    EngineeringEvidencePack,
    EvidenceItem,
    EvidenceReference,
)
from app.knowledge_engine.contracts.hypothesis import Hypothesis
from app.knowledge_engine.contracts.provenance import GeneratorIdentity, Provenance
from app.knowledge_engine.validators.evidence_keyword import (
    CAPABILITY_KEYWORDS,
    EVIDENCE_KEYWORD_VALIDATORS,
    configuration_validator,
    dependency_validator,
    documentation_validator,
    manifest_validator,
)

pytestmark = pytest.mark.asyncio


def _provenance() -> Provenance:
    return Provenance(
        generator=GeneratorIdentity(kind="llm", name="test", version="1.0.0"),
        produced_at=datetime.now(UTC),
        pack_id="pack:1",
        pack_version="v1",
        run_id="pack:1",
    )


def _evidence_item(item_id: str, kind: str, raw_value: str) -> EvidenceItem:
    return EvidenceItem(
        id=item_id,
        kind=kind,
        source_type="documentation",
        reliability_tier=3,
        reference=EvidenceReference(
            repository_id="repo-1", source_type="documentation", locator=item_id
        ),
        raw_value=raw_value,
        provenance=_provenance(),
    )


def _hypothesis(relationship_type: str, evidence_refs: tuple[str, ...]) -> Hypothesis:
    return Hypothesis(
        id=f"hyp:test:{relationship_type}",
        relationship_type=relationship_type,
        source_entity="repo-1:repository",
        target_entity="repo-1:capability:database",
        evidence_refs=evidence_refs,
        explanation="test",
        provenance=_provenance(),
    )


def _pack(items: list[EvidenceItem]) -> EngineeringEvidencePack:
    return EngineeringEvidencePack(
        id="pack:1",
        repository_id="repo-1",
        commit_sha="abc",
        schema_version="v1",
        items=tuple(items),
    )


async def test_manifest_validator_confirms_on_keyword_match() -> None:
    item = _evidence_item(
        "evidence:manifest:0", "repository_manifest", "<artifactId>postgresql</artifactId>"
    )
    hypothesis = _hypothesis("OWNS_DATABASE", (item.id,))

    result = await manifest_validator.validate(hypothesis, _pack([item]))

    assert result.verdict == "confirms"
    assert result.evidence_used == (item.id,)
    assert result.evidence_reliability_tier == 1


async def test_manifest_validator_no_signal_without_keyword() -> None:
    item = _evidence_item(
        "evidence:manifest:0", "repository_manifest", "<artifactId>web-utils</artifactId>"
    )
    hypothesis = _hypothesis("OWNS_DATABASE", (item.id,))

    result = await manifest_validator.validate(hypothesis, _pack([item]))

    assert result.verdict == "no_signal"
    assert result.evidence_used == ()


async def test_manifest_validator_ignores_uncited_evidence() -> None:
    cited = _evidence_item("evidence:manifest:0", "repository_manifest", "no keyword here")
    uncited = _evidence_item("evidence:manifest:1", "repository_manifest", "postgresql driver")
    hypothesis = _hypothesis("OWNS_DATABASE", (cited.id,))

    result = await manifest_validator.validate(hypothesis, _pack([cited, uncited]))

    assert result.verdict == "no_signal"


async def test_manifest_validator_ignores_wrong_evidence_kind() -> None:
    item = _evidence_item("evidence:readme:0", "repository_readme", "uses postgresql")
    hypothesis = _hypothesis("OWNS_DATABASE", (item.id,))

    result = await manifest_validator.validate(hypothesis, _pack([item]))

    assert result.verdict == "no_signal"


async def test_documentation_validator_reads_readme_and_architecture_doc() -> None:
    for kind in ("repository_readme", "repository_architecture_doc"):
        item = _evidence_item(f"evidence:{kind}:0", kind, "we use kafka for events")
        hypothesis = _hypothesis("OWNS_MESSAGE_QUEUE", (item.id,))
        result = await documentation_validator.validate(hypothesis, _pack([item]))
        assert result.verdict == "confirms"


async def test_configuration_validator_reads_repository_config() -> None:
    item = _evidence_item("evidence:config:0", "repository_config", "image: redis:7")
    hypothesis = _hypothesis("CONTAINS_CACHING", (item.id,))

    result = await configuration_validator.validate(hypothesis, _pack([item]))

    assert result.verdict == "confirms"


async def test_dependency_validator_reads_maven_and_python_dependency_nodes() -> None:
    for kind in ("graph_node:MavenDependency", "graph_node:PythonDependency"):
        item = _evidence_item(f"evidence:{kind}:0", kind, '{"artifact_id": "redis-client"}')
        hypothesis = _hypothesis("CONTAINS_CACHING", (item.id,))
        result = await dependency_validator.validate(hypothesis, _pack([item]))
        assert result.verdict == "confirms"


async def test_never_returns_contradicts() -> None:
    item = _evidence_item("evidence:manifest:0", "repository_manifest", "nothing relevant")
    hypothesis = _hypothesis("OWNS_DATABASE", (item.id,))

    for validator in EVIDENCE_KEYWORD_VALIDATORS:
        result = await validator.validate(hypothesis, _pack([item]))
        assert result.verdict != "contradicts"


def test_every_instance_advertises_capability() -> None:
    for validator in EVIDENCE_KEYWORD_VALIDATORS:
        assert validator.name
        assert validator.applies_to == frozenset(CAPABILITY_KEYWORDS)
        assert validator.evidence_kind_prefixes
        assert validator.source_type
        assert validator.reliability_tier == 1


def test_all_thirteen_capability_types_have_keyword_coverage() -> None:
    expected = {
        "OWNS_DATABASE",
        "OWNS_MESSAGE_QUEUE",
        "OWNS_EVENT_SCHEMA",
        "OWNS_API",
        "CONTAINS_INFRASTRUCTURE",
        "CONTAINS_BATCH_JOB",
        "CONTAINS_SCHEDULED_JOB",
        "INTEGRATES_WITH_CLOUD_SERVICE",
        "CONTAINS_FEATURE_FLAG",
        "CONTAINS_AUTHENTICATION",
        "CONTAINS_AUTHORIZATION",
        "CONTAINS_CACHING",
        "CONTAINS_EXTERNAL_API",
    }
    assert set(CAPABILITY_KEYWORDS) == expected
    for keywords in CAPABILITY_KEYWORDS.values():
        assert len(keywords) > 0
