"""`JiraInvestigator.run` — confirms the structured fields JiraTool/
JiraProvider already resolved (status, issue_type, priority, labels,
description) are actually kept on the `work_item` fact now, instead of
being discarded in favor of only the combined `context_text`, and that
`_extract_ticket_sections` runs over the real description.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.agents._contract import Evidence
from app.context_pipeline.models import (
    ProviderCapability,
    Reference,
    ReferenceType,
    ResolvedArtifact,
)
from app.context_pipeline.reasoning.investigation import (
    InvestigationAction,
    Recorder,
    SessionContext,
)
from app.context_pipeline.reasoning.investigators import JiraInvestigator
from app.context_pipeline.reasoning.ledger import Ledger


def _artifact(description: str) -> ResolvedArtifact:
    reference = Reference(
        type=ReferenceType.JIRA_ISSUE,
        provider="jira",
        confidence=1.0,
        raw_value="NPT-29",
        normalized_value="NPT-29",
    )
    return ResolvedArtifact(
        provider="jira",
        capability=ProviderCapability.ISSUE_TRACKER,
        reference=reference,
        title="NPT-29",
        text=f"Jira Bug NPT-29\nDescription:\n{description}",
        evidence=Evidence(kind="tool_call", reference="fetch_jira_issue", summary="ok"),
        raw={
            "issue_key": "NPT-29",
            "summary": "Duplicate records in SCD2 merge",
            "description": description,
            "status": "To Do",
            "issue_type": "Bug",
            "priority": "High",
            "labels": ["data-quality"],
        },
    )


@pytest.mark.asyncio
async def test_work_item_fact_keeps_structured_fields_and_extracted_sections():
    description = (
        "Business Goal: Guarantee exactly-once semantics.\n"
        "Acceptance Criteria:\n"
        "No duplicate current records under concurrent writes."
    )
    ledger = Ledger()
    action = InvestigationAction(
        provider="jira",
        key="fetch_work_item:NPT-29",
        intent="fetch",
        targets="work_item",
        params={
            "reference": {
                "type": "jira_issue",
                "provider": "jira",
                "confidence": 1.0,
                "raw_value": "NPT-29",
                "normalized_value": "NPT-29",
            }
        },
    )
    recorder = Recorder(ledger, action, iteration=1)
    session = SessionContext(db=None, user_id=None)  # type: ignore[arg-type]

    with patch(
        "app.context_pipeline.reasoning.investigators.JiraProvider"
    ) as provider_cls:
        provider_cls.return_value.resolve = AsyncMock(return_value=_artifact(description))
        outcome = await JiraInvestigator().run(action, session, recorder)

    assert outcome.yielded is True
    fact = ledger.facts_of("work_item")[0]
    assert fact.value["status"] == "To Do"
    assert fact.value["issue_type"] == "Bug"
    assert fact.value["priority"] == "High"
    assert fact.value["labels"] == ["data-quality"]
    assert "exactly-once" in fact.value["sections"]["business_goal"]
    assert "No duplicate current records" in fact.value["sections"]["acceptance_criteria"]
    # The full raw text is still kept too — additive, not a replacement.
    assert fact.text == _artifact(description).text
