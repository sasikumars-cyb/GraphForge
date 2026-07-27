"""Tests for app.agents.verification — the generic, language-agnostic
claim/entity verification shared by Planning, Development, and Testing.
"""

from __future__ import annotations

from app.agents.verification import (
    build_evidence_pool,
    check_entity_mismatch,
    verify_claims,
)


class TestCheckEntityMismatch:
    def test_no_op_when_ticket_has_no_acronym(self):
        assert check_entity_mismatch(
            "fix null handling in the export job", "ds-databricks-soco-gpc-c2m-rcs-dataingest"
        ) is None

    def test_flags_mismatch_when_ticket_token_absent_from_repo_name(self):
        # The exact scenario this was built for: ticket names APC, the
        # top-ranked repo is the GPC sibling.
        warning = check_entity_mismatch(
            "Soco_C2M_APC_RCS -> Rate_attribute record is not getting generated",
            "ds-databricks-soco-gpc-c2m-rcs-dataingest",
        )
        assert warning is not None
        assert "APC" in warning
        assert "ds-databricks-soco-gpc-c2m-rcs-dataingest" in warning

    def test_no_warning_when_token_matches_repo_name(self):
        assert check_entity_mismatch(
            "Soco_C2M_GPC_RCS -> some bug", "ds-databricks-soco-gpc-c2m-rcs-dataingest"
        ) is None

    def test_no_op_with_no_selected_repo(self):
        assert check_entity_mismatch("Soco_C2M_APC_RCS ticket", "") is None

    def test_ignores_generic_stopword_acronyms(self):
        # "ETL", "API", "UIS" etc. should never trigger a false mismatch on
        # their own — they're generic technical acronyms, not tenant codes.
        assert check_entity_mismatch(
            "Fix the ETL job so the API returns UIS export correctly",
            "ds-databricks-some-other-dataingest",
        ) is None


class TestVerifyClaims:
    def test_exact_match_is_verified(self):
        pool = build_evidence_pool(["ds-databricks-soco-gpc-c2m-rcs-dataingest"])
        result = verify_claims(["ds-databricks-soco-gpc-c2m-rcs-dataingest"], pool)
        assert result.all_verified
        assert result.unverified == []

    def test_substring_match_is_verified(self):
        pool = build_evidence_pool(["soco_ingest/src/config/pipeline_config.py"])
        result = verify_claims(["pipeline_config.py"], pool)
        assert result.all_verified

    def test_fabricated_claim_is_unverified(self):
        pool = build_evidence_pool(["soco_ingest/src/config/pipeline_config.py"])
        result = verify_claims(["soco_ingest/src/transformers/rate_attribute_transformer.py"], pool)
        assert result.unverified == ["soco_ingest/src/transformers/rate_attribute_transformer.py"]

    def test_fabricated_id_is_unverified(self):
        pool = build_evidence_pool(["component_a", "component_b"])
        result = verify_claims(["f1c96bf9-f0cd-58ee-8c29-f85d4496b5d7"], pool)
        assert not result.all_verified

    def test_empty_claim_is_ignored(self):
        result = verify_claims([""], build_evidence_pool(["x"]))
        assert result.verified == []
        assert result.unverified == []

    def test_case_and_whitespace_insensitive(self):
        pool = build_evidence_pool(["  Rate_Attribute.py  "])
        result = verify_claims(["rate_attribute.py"], pool)
        assert result.all_verified
