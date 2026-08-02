"""ADR 0018 RFC-04 — converts knowledge_engine contracts to/from plain,
JSON-able dicts. Pure functions, no I/O, no compression, no database — the
seam between the DB-agnostic `contracts/` package (ADR 0018: "no
dependencies on Neo4j, Postgres, or any specific generator/validator") and
`memory_service.py`'s persistence concerns.

Explicit round-trip functions rather than a generic `dataclasses.asdict`
+ reconstruction: `asdict` serializes nested dataclasses fine, but has no
inverse — reconstructing typed objects (parsing `datetime` back from an
ISO string, rebuilding `GeneratorIdentity` from a nested dict) needs to be
written by hand either way, so writing both directions explicitly here
keeps the mapping visible and testable rather than implicit.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.knowledge_engine.contracts.confidence import ConfidenceState
from app.knowledge_engine.contracts.correction import CorrectionSource, UserCorrection
from app.knowledge_engine.contracts.evidence import (
    EngineeringEvidencePack,
    EvidenceItem,
    EvidenceReference,
)
from app.knowledge_engine.contracts.explanation import ConfidenceExplanation
from app.knowledge_engine.contracts.provenance import GeneratorIdentity, Provenance


def generator_identity_to_dict(identity: GeneratorIdentity) -> dict[str, Any]:
    return {"kind": identity.kind, "name": identity.name, "version": identity.version}


def generator_identity_from_dict(data: dict[str, Any]) -> GeneratorIdentity:
    return GeneratorIdentity(kind=data["kind"], name=data["name"], version=data["version"])


def provenance_to_dict(provenance: Provenance) -> dict[str, Any]:
    return {
        "generator": generator_identity_to_dict(provenance.generator),
        "produced_at": provenance.produced_at.isoformat(),
        "pack_id": provenance.pack_id,
        "pack_version": provenance.pack_version,
        "run_id": provenance.run_id,
    }


def provenance_from_dict(data: dict[str, Any]) -> Provenance:
    return Provenance(
        generator=generator_identity_from_dict(data["generator"]),
        produced_at=datetime.fromisoformat(data["produced_at"]),
        pack_id=data["pack_id"],
        pack_version=data["pack_version"],
        run_id=data["run_id"],
    )


def evidence_reference_to_dict(reference: EvidenceReference) -> dict[str, Any]:
    return {
        "repository_id": reference.repository_id,
        "source_type": reference.source_type,
        "locator": reference.locator,
        "line": reference.line,
        "key": reference.key,
        "commit_sha": reference.commit_sha,
    }


def evidence_reference_from_dict(data: dict[str, Any]) -> EvidenceReference:
    return EvidenceReference(
        repository_id=data["repository_id"],
        source_type=data["source_type"],
        locator=data["locator"],
        line=data.get("line"),
        key=data.get("key"),
        commit_sha=data.get("commit_sha"),
    )


def evidence_item_to_dict(item: EvidenceItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "kind": item.kind,
        "source_type": item.source_type,
        "reliability_tier": item.reliability_tier,
        "reference": evidence_reference_to_dict(item.reference),
        "raw_value": item.raw_value,
        "provenance": provenance_to_dict(item.provenance),
    }


def evidence_item_from_dict(data: dict[str, Any]) -> EvidenceItem:
    return EvidenceItem(
        id=data["id"],
        kind=data["kind"],
        source_type=data["source_type"],
        reliability_tier=data["reliability_tier"],
        reference=evidence_reference_from_dict(data["reference"]),
        raw_value=data["raw_value"],
        provenance=provenance_from_dict(data["provenance"]),
    )


def evidence_pack_to_dict(pack: EngineeringEvidencePack) -> dict[str, Any]:
    return {
        "id": pack.id,
        "repository_id": pack.repository_id,
        "commit_sha": pack.commit_sha,
        "schema_version": pack.schema_version,
        "items": [evidence_item_to_dict(item) for item in pack.items],
        "produced_at": pack.produced_at.isoformat(),
        "is_delta": pack.is_delta,
        "base_pack_id": pack.base_pack_id,
    }


def evidence_pack_from_dict(data: dict[str, Any]) -> EngineeringEvidencePack:
    return EngineeringEvidencePack(
        id=data["id"],
        repository_id=data["repository_id"],
        commit_sha=data["commit_sha"],
        schema_version=data["schema_version"],
        items=tuple(evidence_item_from_dict(item) for item in data["items"]),
        produced_at=datetime.fromisoformat(data["produced_at"]),
        is_delta=data["is_delta"],
        base_pack_id=data.get("base_pack_id"),
    )


def explanation_to_dict(explanation: ConfidenceExplanation) -> dict[str, Any]:
    return {
        "state": explanation.state.value,
        "confirming_domains": list(explanation.confirming_domains),
        "strongest_domain": explanation.strongest_domain,
        "contradicting_domains": list(explanation.contradicting_domains),
        "why_confidence_increased": explanation.why_confidence_increased,
        "why_confidence_limited": explanation.why_confidence_limited,
        "recommendations": list(explanation.recommendations),
    }


def explanation_from_dict(data: dict[str, Any]) -> ConfidenceExplanation:
    return ConfidenceExplanation(
        state=ConfidenceState(data["state"]),
        confirming_domains=tuple(data["confirming_domains"]),
        strongest_domain=data.get("strongest_domain"),
        contradicting_domains=tuple(data["contradicting_domains"]),
        why_confidence_increased=data["why_confidence_increased"],
        why_confidence_limited=data["why_confidence_limited"],
        recommendations=tuple(data["recommendations"]),
    )


def correction_source_to_dict(source: CorrectionSource) -> dict[str, Any]:
    return {"kind": source.kind, "identity": source.identity, "trust_level": source.trust_level}


def correction_source_from_dict(data: dict[str, Any]) -> CorrectionSource:
    return CorrectionSource(
        kind=data["kind"], identity=data["identity"], trust_level=data["trust_level"]
    )


def user_correction_to_dict(correction: UserCorrection) -> dict[str, Any]:
    return {
        "id": correction.id,
        "relationship_id": correction.relationship_id,
        "source": correction_source_to_dict(correction.source),
        "corrected_state": correction.corrected_state,
        "reason": correction.reason,
        "created_at": correction.created_at.isoformat(),
    }
