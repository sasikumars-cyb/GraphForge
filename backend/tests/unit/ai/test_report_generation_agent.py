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
