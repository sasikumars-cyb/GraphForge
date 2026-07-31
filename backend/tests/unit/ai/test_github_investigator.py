"""Unit tests for GitHubInvestigator.propose() — specifically the
`_FETCHABLE` widening that adds "github_repository" alongside
"github_pull_request"/"github_issue".

Before this change, a bare "owner/repo" mention was detected by
`reference_detection.py` and recorded as a `github_repository` reference
fact, but GitHubInvestigator never proposed a fetch action for it — the
reference was visible in the ledger and then silently never acted on. This
is the regression guard for that gap.
"""

from __future__ import annotations

from app.context_pipeline.reasoning.investigators import GitHubInvestigator
from app.context_pipeline.reasoning.memory import WorkingContext


def _add_reference_fact(state: WorkingContext, ref_type: str, subject: str) -> None:
    evidence = state.ledger.add_evidence(
        provider="parser", action="detect_references", outcome="success", summary="detected"
    )
    state.ledger.add_fact(
        kind="reference",
        subject=subject,
        provider="github",
        evidence_id=evidence.evidence_id,
        value={
            "type": ref_type,
            "provider": "github",
            "confidence": 0.7 if ref_type == "github_repository" else 1.0,
            "raw_value": subject,
            "normalized_value": subject,
        },
    )


def test_proposes_a_fetch_for_a_bare_repository_reference() -> None:
    state = WorkingContext()
    _add_reference_fact(state, "github_repository", "acme/widgets")

    actions = GitHubInvestigator().propose(state)

    assert len(actions) == 1
    assert actions[0].params["reference"]["type"] == "github_repository"
    assert actions[0].params["reference"]["normalized_value"] == "acme/widgets"


def test_still_proposes_a_fetch_for_a_pull_request_reference() -> None:
    state = WorkingContext()
    _add_reference_fact(state, "github_pull_request", "acme/widgets#42")

    actions = GitHubInvestigator().propose(state)

    assert len(actions) == 1
    assert actions[0].params["reference"]["type"] == "github_pull_request"


def test_does_not_propose_a_fetch_for_an_unrelated_reference_type() -> None:
    state = WorkingContext()
    _add_reference_fact(state, "jira_issue", "NPT-6")

    actions = GitHubInvestigator().propose(state)

    assert actions == []


def test_does_not_repropose_an_already_attempted_repository_fetch() -> None:
    state = WorkingContext()
    _add_reference_fact(state, "github_repository", "acme/widgets")
    state.ledger.add_evidence(
        provider="github",
        action="fetch_pull_request:acme/widgets",
        outcome="not_found",
        summary="already tried",
    )

    actions = GitHubInvestigator().propose(state)

    assert actions == []
