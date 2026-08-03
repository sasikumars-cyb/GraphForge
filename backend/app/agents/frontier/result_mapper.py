"""`ResultMapper` — maps a Frontier agent run's pieces (`ExecutionResult`,
LLM narrative, rendered output, metrics, evidence) into the one frozen
envelope every agent must return: `app.agents._contract.AgentOutput`.
Reuses `AgentOutput`/`Confidence`/`Evidence` unmodified — no new result
model, per the RFC's "reuse existing result models where possible".

Confidence scoring here is intentionally minimal and generic: it reflects
how many service calls succeeded, not domain judgment about the result's
quality (that distinction matters — a future agent that wants a smarter
confidence signal computes it in its own `render_response` and passes it
through, rather than this shared mapper guessing at semantics it can't
know).
"""

from __future__ import annotations

from typing import Any

from app.agents._contract import AgentOutput, Confidence, Evidence
from app.agents.frontier.agent_metrics import AgentMetrics
from app.agents.frontier.service_executor import ExecutionResult


def _service_call_evidence(execution: ExecutionResult) -> list[Evidence]:
    evidence: list[Evidence] = []
    error_by_index = {}
    for error in execution.errors:
        # errors are "[index] service: message" — see ServiceExecutor.execute
        index_str, _, rest = error.partition("]")
        try:
            error_by_index[int(index_str.lstrip("["))] = rest.strip()
        except ValueError:
            continue

    for index, call in enumerate(execution.calls):
        failure = error_by_index.get(index)
        evidence.append(
            Evidence(
                kind="tool_call",
                reference=f"engineering_intelligence:{call.service}",
                summary=failure or f"{call.service} completed successfully.",
                status="failed" if failure else "success",
            )
        )
    return evidence


def _confidence_from_execution(execution: ExecutionResult) -> Confidence:
    total = len(execution.calls)
    if total == 0:
        return Confidence(score=0.0, reasoning="No service calls were made.")
    failed = len(execution.errors)
    succeeded = total - failed
    score = succeeded / total
    reasoning = f"{succeeded}/{total} Engineering Intelligence service call(s) succeeded."
    if failed:
        reasoning += f" {failed} failed — see evidence for detail."
    return Confidence(score=score, reasoning=reasoning)


def to_agent_output(
    *,
    agent_id: str,
    subject_id: str,
    execution: ExecutionResult,
    narrative_evidence: Evidence | None,
    rendered: dict[str, Any],
    metrics: AgentMetrics,
    output_ref: str | None = None,
) -> AgentOutput:
    evidence = _service_call_evidence(execution)
    if narrative_evidence is not None:
        evidence.append(narrative_evidence)

    result = dict(rendered)
    result["metrics"] = metrics.to_dict()

    return AgentOutput(
        agent_id=agent_id,
        subject_id=subject_id,
        confidence=_confidence_from_execution(execution),
        evidence=evidence,
        result=result,
        output_ref=output_ref,
    )
