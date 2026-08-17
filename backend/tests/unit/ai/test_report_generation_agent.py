"""Unit tests for the Report Generation Agent (Report V2 Phase 2 rewrite,
ADR 0024) — `app.agents.report_generation.agent.ReportGenerationAgent`.

Covers: the deterministic view model is built and returned regardless of
LLM outcome, only `executive_summary` is LLM-authored, a failed summary
call degrades gracefully (report still succeeds with `executive_summary=
None`), the no-workflow guard, and that no LLM call happens before the
view model is fully built (asserted by patching `_call_llm` to capture
what it was called with — the facts string, never raw stage context).

Same `SimpleNamespace`-based fixture pattern as
`test_engineering_review_agent.py`.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.agents._contract import AgentContext, Subject
from app.agents.report_generation.agent import (
    ReportGenerationAgent,
    ReportGenerationExecutionError,
    ReportGenerationLLMError,
)


def _make_step(result: dict | None, evidence: list[dict] | None = None) -> SimpleNamespace:
    return SimpleNamespace(result=result, evidence=evidence or [], confidence_score=None)


def _make_run(stage: str, status: str, result: dict | None) -> SimpleNamespace:
    return SimpleNamespace(
        workflow_stage=stage,
        status=status,
        steps=[_make_step(result)] if result is not None else [],
        created_at=datetime.now(UTC),
    )


def _make_workflow(
    runs: list | None = None,
    title: str = "Fix the flaky timeout",
    original_prompt: str = "Fix it",
) -> SimpleNamespace:
    return SimpleNamespace(
        runs=runs or [],
        title=title,
        original_prompt=original_prompt,
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _make_context(workflow: SimpleNamespace | None) -> AgentContext:
    subject = Subject(
        subject_id="freetext:abc123", subject_type="freetext", display_name="Fix the flaky timeout"
    )
    extras: dict = {"db": AsyncMock()}
    if workflow is not None:
        extras["workflow"] = workflow
    return AgentContext(subject=subject, goal="generate_report", extras=extras)


def _context_discovery_result(with_hypotheses: bool = True) -> dict:
    result: dict = {
        "original_request": "why is the timeout flaky",
        "repositories": [{"name": "agent-runtime", "selected": True}],
        "discovery_report": {
            "investigation": [
                {
                    "iteration": 1,
                    "provider": "graph",
                    "action": "a",
                    "outcome": "success",
                    "summary": "s",
                    "intent": "i",
                }
            ],
            "findings": [],
            "gaps": [],
        },
    }
    if with_hypotheses:
        result["reasoning_summary"] = {
            "synthesis_state": "completed",
            "hypotheses": [
                {"description": "the timeout is too low", "status": "supported", "confidence": 0.8}
            ],
            "contradictions": [],
        }
    else:
        result["reasoning_summary"] = {
            "synthesis_state": "not_run",
            "hypotheses": [],
            "contradictions": [],
        }
    return result


class TestReportGenerationAgent:
    @pytest.mark.asyncio
    async def test_no_workflow_raises(self):
        agent = ReportGenerationAgent()
        with pytest.raises(ReportGenerationExecutionError):
            await agent.run(_make_context(None))

    @pytest.mark.asyncio
    async def test_view_model_is_built_and_returned_on_success(self):
        workflow = _make_workflow(
            runs=[_make_run("context_discovery", "completed", _context_discovery_result())]
        )
        agent = ReportGenerationAgent()
        with patch(
            "app.agents.report_generation.agent._call_llm",
            new=AsyncMock(
                return_value=json.dumps(
                    {"title": "Timeout investigation", "executive_summary": "It's the timeout."}
                )
            ),
        ):
            output = await agent.run(_make_context(workflow))

        assert output.result["title"] == "Timeout investigation"
        vm = output.result["view_model"]
        assert vm["header"]["question"] == "why is the timeout flaky"
        assert vm["hypotheses"]["synthesis_state"] == "completed"
        assert vm["hypotheses"]["items"][0]["entry"]["statement"] == "the timeout is too low"
        assert vm["executive_summary"] == "It's the timeout."
        assert output.result["html"]  # fallback HTML still produced

    @pytest.mark.asyncio
    async def test_llm_failure_does_not_block_the_report(self):
        workflow = _make_workflow(
            runs=[_make_run("context_discovery", "completed", _context_discovery_result())]
        )
        agent = ReportGenerationAgent()
        with patch(
            "app.agents.report_generation.agent._call_llm",
            new=AsyncMock(side_effect=ReportGenerationLLMError("provider timed out")),
        ):
            output = await agent.run(_make_context(workflow))

        vm = output.result["view_model"]
        assert vm["executive_summary"] is None
        # Every other section is still fully populated — an LLM outage
        # never removes structured data that was already decided.
        assert vm["hypotheses"]["items"][0]["entry"]["statement"] == "the timeout is too low"
        failed_evidence = [e for e in output.evidence if e.summary.startswith("FAILED:")]
        assert failed_evidence

    @pytest.mark.asyncio
    async def test_degraded_synthesis_state_is_carried_into_view_model(self):
        workflow = _make_workflow(
            runs=[
                _make_run(
                    "context_discovery",
                    "completed",
                    _context_discovery_result(with_hypotheses=False),
                )
            ]
        )
        agent = ReportGenerationAgent()
        with patch(
            "app.agents.report_generation.agent._call_llm",
            new=AsyncMock(return_value=json.dumps({"title": "t", "executive_summary": "s"})),
        ):
            output = await agent.run(_make_context(workflow))

        vm = output.result["view_model"]
        assert vm["hypotheses"]["synthesis_state"] == "not_run"
        assert vm["hypotheses"]["availability"]["status"] == "unavailable"
        assert vm["hypotheses"]["items"] == []

    @pytest.mark.asyncio
    async def test_llm_call_receives_only_already_decided_facts_not_raw_stage_context(self):
        # The architectural rule under test: the LLM prompt must never
        # contain raw stage JSON/context — only the plain-text facts
        # `_summary_prompt_facts` derives from the already-built view
        # model (ADR §13).
        workflow = _make_workflow(
            runs=[_make_run("context_discovery", "completed", _context_discovery_result())]
        )
        agent = ReportGenerationAgent()
        mock_call = AsyncMock(return_value=json.dumps({"title": "t", "executive_summary": "s"}))
        with patch("app.agents.report_generation.agent._call_llm", new=mock_call):
            await agent.run(_make_context(workflow))

        prompt_sent = mock_call.call_args.kwargs["user_prompt"]
        assert "the timeout is too low" in prompt_sent  # the decided top hypothesis, by design
        assert '"original_request"' not in prompt_sent  # never the raw JSON stage result
        assert "discovery_report" not in prompt_sent


def _prot5749_shaped_workflow() -> SimpleNamespace:
    """A workflow shaped like the real post–Engineering Review case this
    document format was fixed for: one strongly-supported but unverified
    hypothesis, a second competing one, an unresolved contradiction, an
    open knowledge gap, and a `needs_revision` verdict. Deliberately
    generic data — no ticket, repository, or file from any real project."""
    return _make_workflow(
        runs=[
            _make_run(
                "context_discovery",
                "completed",
                {
                    "original_request": "Filtered rows are still being exported",
                    "repositories": [{"name": "ingest-service", "selected": True}],
                    "reasoning_summary": {
                        "synthesis_state": "completed",
                        "hypotheses": [
                            {
                                "description": "the export filter is the wrong place",
                                "status": "supported",
                                "confidence": 0.95,
                                "supporting_evidence": ["the filter reads the unit column"],
                                "contradicting_evidence": [],
                            },
                            {
                                "description": "the rows are created upstream at ingest",
                                "status": "unknown",
                                "confidence": 0.55,
                                "supporting_evidence": [],
                                "contradicting_evidence": [],
                            },
                        ],
                        "contradictions": [
                            {
                                "description": "the ticket and the raw input disagree",
                                "evidence_for": ["the ticket lists the value as exported"],
                                "evidence_against": ["the raw input never contains that value"],
                                "resolved": False,
                                "resolution_note": "",
                            }
                        ],
                    },
                    "discovery_report": {
                        "investigation": [
                            {
                                "iteration": i,
                                "provider": "graph",
                                "action": "a",
                                "outcome": "success",
                                "summary": f"step {i}",
                                "intent": "i",
                            }
                            for i in range(1, 26)
                        ],
                        "findings": [
                            {
                                "kind": "repository",
                                "total": 1,
                                "items": [
                                    {
                                        "subject": "ingest-service",
                                        "verified": True,
                                        "evidence": {"summary": "indexed in the graph"},
                                    }
                                ],
                            }
                        ],
                        "gaps": [
                            {
                                "gap_id": "g1",
                                "summary": "where the unexpected value is introduced",
                                "status": "open",
                                "severity": "advisory",
                            }
                        ],
                    },
                },
            ),
            _make_run(
                "engineering_review",
                "completed",
                {"readiness_status": "needs_revision", "blocking_issues": []},
            ),
        ]
    )


class TestGeneratedDocument:
    """Asserts against the *generated document*, not the builder — the
    view model the frontend renders and the HTML fallback consumers read."""

    async def _run(self, workflow: SimpleNamespace) -> dict:
        agent = ReportGenerationAgent()
        with patch(
            "app.agents.report_generation.agent._call_llm",
            new=AsyncMock(return_value=json.dumps({"title": "t", "executive_summary": "s"})),
        ):
            output = await agent.run(_make_context(workflow))
        return output.result

    @pytest.mark.asyncio
    async def test_document_separates_confirmed_findings_from_the_95_percent_hypothesis(self):
        result = await self._run(_prot5749_shaped_workflow())
        model = result["view_model"]

        confirmed = [f["statement"] for f in model["findings"]["items"]]
        assert confirmed == ["repository: ingest-service"]
        assert model["hypotheses"]["items"][0]["entry"]["confidence"] == 0.95
        assert model["hypotheses"]["items"][0]["verification_status"] is None
        assert "the export filter is the wrong place" not in confirmed

    @pytest.mark.asyncio
    async def test_document_states_the_engineering_review_outcome_in_words(self):
        model = (await self._run(_prot5749_shaped_workflow()))["view_model"]
        outcome = model["review_outcome"]

        assert outcome["outcome_label"] == "Needs Revision"
        assert outcome["readiness"] == "needs_revision"
        assert outcome["reasons"]
        assert outcome["recommendation"].startswith("Do not implement")
        assert "competing explanations" in outcome["recommendation"]

    @pytest.mark.asyncio
    async def test_document_never_disagrees_with_itself_about_blocking_items(self):
        model = (await self._run(_prot5749_shaped_workflow()))["view_model"]
        html = (await self._run(_prot5749_shaped_workflow()))["html"]

        assert model["next_actions"]["blocking_count"] == 1
        assert model["next_actions"]["advisory_count"] == 1
        assert model["review_outcome"]["blocking_count"] == 1
        assert model["review_outcome"]["advisory_count"] == 1
        # And the rendered document says it once, from those same counts.
        assert "1 blocking, 1 advisory" in html

    @pytest.mark.asyncio
    async def test_document_labels_both_confidence_numbers(self):
        model = (await self._run(_prot5749_shaped_workflow()))["view_model"]
        breakdown = model["confidence"]["breakdown"]
        assert breakdown["top_hypothesis_confidence"] == 0.95
        assert breakdown["top_hypothesis_label"] == "Root-cause candidate confidence"
        assert breakdown["overall_label"] == "Overall resolution confidence"
        assert breakdown["divergence_note"] is None or "different things" in (
            breakdown["divergence_note"]
        )

    @pytest.mark.asyncio
    async def test_html_document_is_a_decision_document_not_an_execution_log(self):
        html = (await self._run(_prot5749_shaped_workflow()))["html"]

        for heading in (
            "1. Problem statement",
            "2. Investigation summary",
            "3. Confirmed findings",
            "4. Potential root cause / hypotheses (unconfirmed)",
            "5. Evidence",
            "6. Contradictions / knowledge gaps",
            "7. Engineering Review outcome",
            "8. Recommended next steps",
            "9. Confidence &amp; readiness",
            "10. Evidence / provenance",
        ):
            assert heading in html, heading

        assert "Engineering Review Outcome: Needs Revision" in html
        assert "Do not implement the proposed change yet." in html
        assert "Required resolution:" in html
        # The 25-step execution trail must not dominate the document — it
        # is summarized as provenance, never enumerated here.
        assert "step 14" not in html
        assert "25 investigation steps were recorded" in html

    @pytest.mark.asyncio
    async def test_summary_prompt_forbids_presenting_a_hypothesis_as_a_root_cause(self):
        workflow = _prot5749_shaped_workflow()
        agent = ReportGenerationAgent()
        mock_call = AsyncMock(return_value=json.dumps({"title": "t", "executive_summary": "s"}))
        with patch("app.agents.report_generation.agent._call_llm", new=mock_call):
            await agent.run(_make_context(workflow))
        prompt = mock_call.call_args.kwargs["user_prompt"]

        # The facts handed to the LLM must carry the distinction itself —
        # the summary cannot narrate a separation it was never told about.
        assert "Engineering Review outcome: Needs Revision" in prompt
        assert "Root-cause candidate confidence" in prompt
        assert "Overall resolution confidence" in prompt
        assert "UNCONFIRMED unless verified" in prompt
        assert "UNRESOLVED — blocking" in prompt
        assert "Open items: 1 blocking, 1 advisory." in prompt
        assert "Never present a hypothesis as the root cause" in prompt

    @pytest.mark.asyncio
    async def test_an_approved_investigation_reads_as_an_approval(self):
        workflow = _make_workflow(
            runs=[
                _make_run("context_discovery", "completed", _context_discovery_result()),
                _make_run(
                    "engineering_review",
                    "completed",
                    {"readiness_status": "ready", "blocking_issues": []},
                ),
            ]
        )
        result = await self._run(workflow)
        outcome = result["view_model"]["review_outcome"]
        assert outcome["outcome_label"] == "Approved"
        assert outcome["recommendation"].startswith("Proceed with implementation as reviewed.")
        assert "Engineering Review Outcome: Approved" in result["html"]
        assert "Do not implement" not in result["html"]
