"""Tests for `investigators._extract_ticket_sections` — deterministic,
section-heading-based extraction of Problem/Business Goal/Acceptance
Criteria/Constraints/Dependencies from a Jira description, replacing the
prior behavior of discarding everything except the combined raw text.
"""

from __future__ import annotations

from app.context_pipeline.reasoning.investigators import _extract_ticket_sections


class TestHeadingAloneOnItsOwnLine:
    def test_extracts_section_with_content_on_following_lines(self):
        description = (
            "Acceptance Criteria:\n"
            "User can log in with email and password.\n"
            "Session persists across page reloads.\n"
            "\n"
            "Constraints:\n"
            "Must not break existing SSO flow."
        )
        sections = _extract_ticket_sections(description)
        assert "email and password" in sections["acceptance_criteria"]
        assert "SSO flow" in sections["constraints"]

    def test_heading_without_trailing_colon(self):
        description = "Problem\nThe login page throws a 500 error on submit."
        sections = _extract_ticket_sections(description)
        assert "500 error" in sections["problem"]

    def test_short_alias_ac_is_recognized(self):
        description = "AC:\nExactly one current record per key after merge."
        sections = _extract_ticket_sections(description)
        assert "one current record" in sections["acceptance_criteria"]


class TestInlineContentOnSameLine:
    def test_extracts_content_immediately_after_colon(self):
        description = "Business Goal: Reduce duplicate records in the warehouse."
        sections = _extract_ticket_sections(description)
        assert sections["business_goal"] == "Reduce duplicate records in the warehouse."

    def test_inline_content_continues_onto_following_lines(self):
        description = (
            "Dependencies: Requires the new dedup transformer to ship first.\n"
            "Also needs schema migration NPT-101."
        )
        sections = _extract_ticket_sections(description)
        assert "dedup transformer" in sections["dependencies"]
        assert "NPT-101" in sections["dependencies"]


class TestNoStructureAtAll:
    def test_plain_prose_with_no_headings_returns_empty(self):
        description = (
            "SCD2 merge produces duplicate current records when source "
            "contains duplicate keys from Kafka redelivery."
        )
        assert _extract_ticket_sections(description) == {}

    def test_empty_description_returns_empty(self):
        assert _extract_ticket_sections("") == {}

    def test_heading_shaped_word_mid_sentence_is_not_falsely_matched(self):
        # "Goal" appears but not as a real heading — it's mid-sentence,
        # on a line that doesn't end right after the word or a colon.
        description = "The goal is unclear without more context from the reporter."
        assert _extract_ticket_sections(description) == {}


class TestMultipleSectionsInOneDescription:
    def test_all_recognized_sections_extracted_independently(self):
        description = (
            "Problem: Duplicate records appear after concurrent writes.\n"
            "Business Goal: Guarantee exactly-once semantics.\n"
            "Acceptance Criteria:\n"
            "- No duplicates under concurrent load\n"
            "- Existing tests still pass\n"
            "Constraints: Must not change the public API.\n"
            "Dependencies: None."
        )
        sections = _extract_ticket_sections(description)
        assert set(sections.keys()) == {
            "problem",
            "business_goal",
            "acceptance_criteria",
            "constraints",
            "dependencies",
        }
        assert "exactly-once" in sections["business_goal"]
        assert "No duplicates" in sections["acceptance_criteria"]
