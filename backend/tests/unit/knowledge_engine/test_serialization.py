"""Tests for app.knowledge_engine.serialization — ADR 0018 RFC-04.

Pure round-trip tests: contract -> dict -> contract must reproduce the
original object exactly. No database involved.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.knowledge_engine.contracts.confidence import ConfidenceState
from app.knowledge_engine.contracts.correction import CorrectionSource, UserCorrection
from app.knowledge_engine.contracts.evidence import (
    EngineeringEvidencePack,
    EvidenceItem,
    EvidenceReference,
)
from app.knowledge_engine.contracts.provenance import GeneratorIdentity, Provenance
from app.knowledge_engine.serialization import (
    evidence_item_from_dict,
    evidence_item_to_dict,
    evidence_pack_from_dict,
    evidence_pack_to_dict,
    evidence_reference_from_dict,
    evidence_reference_to_dict,
    generator_identity_from_dict,
    generator_identity_to_dict,
    provenance_from_dict,
    provenance_to_dict,
    user_correction_to_dict,
)


def _identity() -> GeneratorIdentity:
    return GeneratorIdentity(kind="deterministic", name="test_parser", version="1.0.0")


def _provenance() -> Provenance:
    return Provenance(
        generator=_identity(),
        produced_at=datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC),
        pack_id="pack-1",
        pack_version="v1",
        run_id="pack-1",
    )


def _evidence_item() -> EvidenceItem:
    return EvidenceItem(
        id="evidence-1",
        kind="graph_node:Repository",
        source_type="code",
        reliability_tier=3,
        reference=EvidenceReference(
            repository_id="repo-1",
            source_type="code",
            locator="src/app.py",
            line=42,
            key="repo-1:repository",
            commit_sha="abc123",
        ),
        raw_value='{"language": "python"}',
        provenance=_provenance(),
    )


class TestGeneratorIdentityRoundTrip:
    def test_round_trips_exactly(self):
        identity = _identity()
        assert generator_identity_from_dict(generator_identity_to_dict(identity)) == identity


class TestProvenanceRoundTrip:
    def test_round_trips_exactly(self):
        provenance = _provenance()
        assert provenance_from_dict(provenance_to_dict(provenance)) == provenance


class TestEvidenceReferenceRoundTrip:
    def test_round_trips_exactly_with_all_optional_fields(self):
        reference = EvidenceReference(
            repository_id="repo-1",
            source_type="code",
            locator="src/app.py",
            line=42,
            key="k",
            commit_sha="abc",
        )
        assert evidence_reference_from_dict(evidence_reference_to_dict(reference)) == reference

    def test_round_trips_exactly_with_no_optional_fields(self):
        reference = EvidenceReference(
            repository_id="repo-1", source_type="docs", locator="README.md"
        )
        assert evidence_reference_from_dict(evidence_reference_to_dict(reference)) == reference


class TestEvidenceItemRoundTrip:
    def test_round_trips_exactly(self):
        item = _evidence_item()
        assert evidence_item_from_dict(evidence_item_to_dict(item)) == item


class TestEvidencePackRoundTrip:
    def test_round_trips_exactly_with_items(self):
        pack = EngineeringEvidencePack(
            id="pack-1",
            repository_id="repo-1",
            commit_sha="abc123",
            schema_version="v1",
            items=(_evidence_item(),),
            produced_at=datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC),
        )
        assert evidence_pack_from_dict(evidence_pack_to_dict(pack)) == pack

    def test_round_trips_exactly_when_empty(self):
        pack = EngineeringEvidencePack(
            id="pack-empty",
            repository_id="repo-1",
            commit_sha="abc123",
            schema_version="v1",
            produced_at=datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC),
        )
        assert evidence_pack_from_dict(evidence_pack_to_dict(pack)) == pack

    def test_round_trips_delta_pack_fields(self):
        pack = EngineeringEvidencePack(
            id="pack-2",
            repository_id="repo-1",
            commit_sha="abc123",
            schema_version="v1",
            produced_at=datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC),
            is_delta=True,
            base_pack_id="pack-1",
        )
        round_tripped = evidence_pack_from_dict(evidence_pack_to_dict(pack))
        assert round_tripped == pack
        assert round_tripped.is_delta is True
        assert round_tripped.base_pack_id == "pack-1"

    def test_survives_a_real_json_dumps_loads_cycle(self):
        """Not just object round-trip — the actual serialization format
        used for compression (json.dumps -> gzip -> ... -> json.loads)."""
        import json

        pack = EngineeringEvidencePack(
            id="pack-1",
            repository_id="repo-1",
            commit_sha="abc123",
            schema_version="v1",
            items=(_evidence_item(),),
            produced_at=datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC),
        )
        json_text = json.dumps(evidence_pack_to_dict(pack), sort_keys=True)
        reconstructed = evidence_pack_from_dict(json.loads(json_text))
        assert reconstructed == pack


class TestUserCorrectionSerialization:
    def test_to_dict_captures_every_field(self):
        correction = UserCorrection(
            id="correction-1",
            relationship_id="relationship-1",
            source=CorrectionSource(kind="human", identity="user-1", trust_level=1.0),
            corrected_state=ConfidenceState.REJECTED,
            reason="This service was decommissioned.",
            created_at=datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC),
        )
        data = user_correction_to_dict(correction)
        assert data["source"]["kind"] == "human"
        assert data["corrected_state"] == "rejected"
        assert data["reason"] == "This service was decommissioned."
