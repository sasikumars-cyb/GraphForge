"""Unit tests for `ResultMapper.to_agent_output` — pure, no I/O."""

from __future__ import annotations

from app.agents._contract import Evidence
from app.agents.frontier.agent_metrics import AgentMetrics
from app.agents.frontier.result_mapper import to_agent_output
from app.agents.frontier.service_executor import ArchitectureInsightCall, DependencyQueryCall


def _execution(errors: tuple[str, ...] = ()):
    from app.agents.frontier.service_executor import ExecutionResult

    calls = (
        DependencyQueryCall(repository_ids=()),
        ArchitectureInsightCall(repository_ids=()),
    )
    results = tuple(None if any(f"[{i}]" in e for e in errors) else object() for i in range(2))
    return ExecutionResult(calls=calls, results=results, errors=errors)


def test_to_agent_output_scores_confidence_by_success_ratio() -> None:
    output = to_agent_output(
        agent_id="repository_understanding",
        subject_id="repo:x",
        execution=_execution(),
        narrative_evidence=None,
        rendered={"summary": "ok"},
        metrics=AgentMetrics(),
    )

    assert output.confidence.score == 1.0
    assert output.agent_id == "repository_understanding"
    assert output.subject_id == "repo:x"
    assert output.result["summary"] == "ok"
    assert "metrics" in output.result


def test_to_agent_output_lowers_confidence_on_partial_failure() -> None:
    output = to_agent_output(
        agent_id="repository_understanding",
        subject_id="repo:x",
        execution=_execution(errors=("[0] dependency_query: boom",)),
        narrative_evidence=None,
        rendered={},
        metrics=AgentMetrics(),
    )

    assert output.confidence.score == 0.5
    # UX audit P2.1: reasoning is plain, domain language now, not an
    # internal "N service call(s)" phrasing — the raw counts still exist
    # in full on the per-call Evidence items (asserted below).
    assert "could not be reached" in output.confidence.reasoning
    assert "service call" not in output.confidence.reasoning
    failed_evidence = [e for e in output.evidence if e.status == "failed"]
    assert len(failed_evidence) == 1
    assert failed_evidence[0].reference == "engineering_intelligence:dependency_query"


def test_to_agent_output_includes_narrative_evidence_when_given() -> None:
    narrative_evidence = Evidence(
        kind="llm_reasoning",
        reference="prompt_builder:invoke_llm_json",
        summary="ok",
        status="success",
    )
    output = to_agent_output(
        agent_id="repository_understanding",
        subject_id="repo:x",
        execution=_execution(),
        narrative_evidence=narrative_evidence,
        rendered={},
        metrics=AgentMetrics(),
    )

    assert narrative_evidence in output.evidence


def test_to_agent_output_zero_calls_yields_zero_confidence() -> None:
    from app.agents.frontier.service_executor import ExecutionResult

    output = to_agent_output(
        agent_id="repository_understanding",
        subject_id="repo:x",
        execution=ExecutionResult(calls=(), results=(), errors=()),
        narrative_evidence=None,
        rendered={},
        metrics=AgentMetrics(),
    )

    assert output.confidence.score == 0.0
    assert output.evidence == []
