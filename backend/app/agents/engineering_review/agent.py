"""Engineering Review Agent — Blueprint Readiness capability.

Implements the IAgent protocol for goal=review_readiness. Unlike Planning/
Development/Testing, this agent runs no graph tools of its own — it
synthesizes over the prior three stages' structured results.

Those results are read directly via get_stage_result() (the same
structured-artifact-reader every deterministic execution agent already
uses — see app.agents.git_ops._artifact_reader), NOT via
workflow_service.build_stage_context()/resolve_freetext(). That freetext
chain hard-truncates its concatenated string to 256 characters
(app/context/resolvers/freetext.py), which meant Development's and
Testing's summaries almost never survived to reach this agent — the root
cause of readiness reports incorrectly claiming "No repositories
identified" / "No risks" / "No test strategy" for blueprints that clearly
had them. context.subject (built via that same freetext chain) is still
used here, but only for subject_id/logging and the short original
objective line — never as the source of blueprint content.

Reviews planning artifacts — implementation completeness, repository/
component selection, risks, dependencies, test strategy. Never a git
diff; that is the separate, unchanged Review Agent (review_pr), which
this agent does not call and which never runs inside a Planning workflow.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.agents._contract import (
    AgentContext,
    AgentOutput,
    Confidence,
    Evidence,
)
from app.agents.engineering_review.schemas import (
    CompletenessFinding,
    DependencyAssessment,
    EngineeringReadinessReport,
    RiskAssessment,
)
from app.agents.git_ops._artifact_reader import get_stage_result
from app.agents.prompt_utils import render_prompt_template
from app.ai.providers.base import LLMRequestOptions, ResponseFormat
from app.ai.providers.factory import create_llm_provider
from app.core.exceptions import AppError

logger = logging.getLogger(__name__)

_PROMPT_VERSION = "1.0"
_PROMPT_DIR = Path(__file__).parent / "prompts"
_MAX_CONTEXT_CHARS = 8_000

# Three stages' worth of full structured artifacts, vs. the single-stage
# graph_context budget above — this agent has no graph_context of its own,
# so this is the only real cap on prompt size now that blueprint_context
# is no longer implicitly capped at 256 chars by the freetext chain.
_MAX_BLUEPRINT_CONTEXT_CHARS = 24_000

_READINESS_CONFIDENCE = {
    "ready": 0.9,
    "needs_revision": 0.6,
    "not_ready": 0.3,
}


# ---------------------------------------------------------------------------
# Full-artifact context builder — reads each prior stage's real, untruncated
# result and renders it as a labeled block. Field names are cross-checked
# directly against planning/schemas.py, development/schemas.py, and
# testing/schemas.py; nothing here is guessed.
# ---------------------------------------------------------------------------


def _join(items: list[Any], render: Callable[[Any], str]) -> str:
    return "; ".join(render(i) for i in items)


def _format_planning_block(result: dict[str, Any] | None) -> str:
    if not result:
        return "### Planning Stage\n(No completed Planning stage result available.)"

    lines = [
        "### Planning Stage",
        f"Summary: {result.get('executive_summary') or '(none)'}",
    ]
    if steps := result.get("implementation_steps"):
        lines.append(
            "Implementation steps: "
            + _join(
                steps,
                lambda s: (
                    f"{s.get('order', '?')}. {s.get('description', '')}"
                    f" (risk: {s.get('risk_note') or 'none noted'})"
                ),
            )
        )
    if comps := result.get("affected_components"):
        lines.append("Affected components: " + ", ".join(comps))
    if topics := result.get("kafka_topics_involved"):
        lines.append("Kafka topics involved: " + ", ".join(topics))
    if risks := result.get("risk_considerations"):
        lines.append("Risk considerations: " + ", ".join(risks))
    if repos := result.get("repositories_consulted"):
        lines.append("Repositories consulted: " + ", ".join(repos))
    return "\n".join(lines)


def _format_development_block(result: dict[str, Any] | None) -> str:
    if not result:
        return "### Development Stage\n(No completed Development stage result available.)"

    lines = [
        "### Development Stage",
        f"Summary: {result.get('executive_summary') or '(none)'}",
    ]
    if repos := result.get("repositories"):
        lines.append(
            "Repositories: "
            + _join(
                repos,
                lambda r: f"{r.get('name', '?')} ({r.get('owner', '?')}) — {r.get('reason', '')}",
            )
        )
    if comps := result.get("components"):
        lines.append(
            "Components: "
            + _join(
                comps,
                lambda c: (
                    f"{c.get('name', '?')} [{c.get('component_type', '')}]"
                    f" {c.get('file_path', '')} — {c.get('change_description', '')}"
                ),
            )
        )
    if deps := result.get("dependencies"):
        lines.append(
            "Dependencies: "
            + _join(
                deps,
                lambda d: (
                    f"{d.get('source', '?')} —[{d.get('relationship', '')}]→ {d.get('target', '?')}"
                    f" (risk: {d.get('risk_note') or 'none noted'})"
                ),
            )
        )
    if reuse := result.get("reusable_implementations"):
        lines.append(
            "Reusable implementations: "
            + _join(
                reuse,
                lambda r: (
                    f"{r.get('name', '?')} ({r.get('repository', '')}) — {r.get('reason', '')}"
                ),
            )
        )
    if phases := result.get("implementation_phases"):
        lines.append(
            "Implementation phases: "
            + _join(
                phases,
                lambda p: (
                    f"{p.get('order', '?')}. {p.get('title', '')}"
                    f" [{p.get('estimated_complexity') or 'unspecified'} complexity]"
                    f" — {p.get('description', '')}"
                ),
            )
        )
    if risks := result.get("risks"):
        lines.append(
            "Risks: "
            + _join(
                risks,
                lambda r: (
                    f"{r.get('description', '')} [{r.get('severity') or 'unspecified'}]"
                    f" — mitigation: {r.get('mitigation') or 'none stated'}"
                ),
            )
        )
    if recs := result.get("recommendations"):
        lines.append("Recommendations: " + "; ".join(recs))
    return "\n".join(lines)


def _format_testing_block(result: dict[str, Any] | None) -> str:
    if not result:
        return "### Testing Stage\n(No completed Testing stage result available.)"

    lines = [
        "### Testing Stage",
        f"Summary: {result.get('executive_summary') or '(none)'}",
    ]
    if scope := result.get("test_scope"):
        in_scope = ", ".join(scope.get("in_scope", [])) or "(none stated)"
        out_scope = ", ".join(scope.get("out_of_scope", [])) or "(none stated)"
        lines.append(f"Test scope: in scope — {in_scope}; out of scope — {out_scope}")
    if repos := result.get("affected_repositories"):
        lines.append("Affected repositories: " + ", ".join(repos))
    if comps := result.get("affected_components"):
        lines.append("Affected components: " + ", ".join(comps))
    if regression := result.get("regression_tests"):
        lines.append(
            "Regression tests: "
            + _join(
                regression,
                lambda t: (
                    f"{t.get('component', '?')}: {t.get('description', '')}"
                    f" [{t.get('priority') or 'unspecified'}]"
                ),
            )
        )
    if integration := result.get("integration_tests"):
        lines.append(
            "Integration tests: "
            + _join(
                integration,
                lambda t: (
                    f"{t.get('source_component', '?')} → {t.get('target_component', '?')}"
                    f" [{t.get('relationship', '')}]: {t.get('description', '')}"
                ),
            )
        )
    if edge_cases := result.get("edge_cases"):
        lines.append(
            "Edge cases: "
            + _join(
                edge_cases,
                lambda e: f"{e.get('description', '')} [{e.get('severity') or 'unspecified'}]",
            )
        )
    if envs := result.get("environment_requirements"):
        lines.append(
            "Environment requirements: "
            + _join(envs, lambda e: f"{e.get('name', '?')} — {e.get('description', '')}")
        )
    if exec_order := result.get("execution_order"):
        lines.append(
            "Execution order: "
            + _join(
                exec_order,
                lambda p: (
                    f"{p.get('order', '?')}. {p.get('title', '')} — {p.get('description', '')}"
                ),
            )
        )
    if automation := result.get("automation_candidates"):
        lines.append(
            "Automation candidates: "
            + _join(automation, lambda a: f"{a.get('description', '')} ({a.get('test_type', '')})")
        )
    if manual := result.get("manual_validations"):
        lines.append(
            "Manual validations: "
            + _join(manual, lambda m: f"{m.get('description', '')} — {m.get('reason', '')}")
        )
    if risks := result.get("risks"):
        lines.append(
            "Risks: "
            + _join(
                risks,
                lambda r: (
                    f"{r.get('description', '')} [{r.get('severity') or 'unspecified'}]"
                    f" — mitigation: {r.get('mitigation') or 'none stated'}"
                ),
            )
        )
    if recs := result.get("recommendations"):
        lines.append("Recommendations: " + "; ".join(recs))
    return "\n".join(lines)


def _build_blueprint_context(
    original_objective: str,
    planning_result: dict[str, Any] | None,
    development_result: dict[str, Any] | None,
    testing_result: dict[str, Any] | None,
) -> str:
    context = "\n\n".join(
        [
            f"## Original Objective\n{original_objective}",
            _format_planning_block(planning_result),
            _format_development_block(development_result),
            _format_testing_block(testing_result),
        ]
    )
    if len(context) > _MAX_BLUEPRINT_CONTEXT_CHARS:
        logger.warning(
            "engineering_review_blueprint_context_truncated original_chars=%d max_chars=%d",
            len(context),
            _MAX_BLUEPRINT_CONTEXT_CHARS,
        )
        context = context[:_MAX_BLUEPRINT_CONTEXT_CHARS]
    return context


# ---------------------------------------------------------------------------
# LLM call — engineering-review-specific, same mechanics as Planning/
# Development/Testing/Code Generation (via create_llm_provider()), separate
# error class per the existing per-agent convention.
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = (
    "You are a Principal Engineer performing a blueprint readiness review. "
    "Respond ONLY with valid JSON matching the requested schema. "
    "Do not include markdown fences or commentary outside the JSON object."
)


class EngineeringReviewLLMError(AppError):
    status_code = 502
    error_code = "engineering_review_llm_error"


async def _call_llm(user_prompt: str, model: str | None = None) -> str:
    try:
        provider = create_llm_provider(model=model)
        response = await provider.complete(
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            options=LLMRequestOptions(response_format=ResponseFormat.JSON),
        )
    except AppError as exc:
        error = EngineeringReviewLLMError(exc.message)
        error.provider_error = getattr(exc, "provider_error", None)  # type: ignore[attr-defined]
        raise error from exc
    return response.text


def _render_prompt(blueprint_context: str) -> str:
    """Render engineering_review.md — this agent has no graph_context of
    its own, so only {{ task_description }} (the full blueprint text
    _build_blueprint_context() assembled) is substituted."""
    return render_prompt_template(
        _PROMPT_DIR / "engineering_review.md", blueprint_context, "", _MAX_CONTEXT_CHARS
    )


def _parse_llm_response(raw: str, goal: str) -> EngineeringReadinessReport:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise EngineeringReviewLLMError(f"LLM response is not valid JSON: {exc}") from exc

    completeness_findings = [
        CompletenessFinding(
            area=f.get("area", ""),
            status=f.get("status", ""),
            detail=f.get("detail", ""),
        )
        for f in data.get("completeness_findings", [])
    ]

    risk_assessment = [
        RiskAssessment(
            description=r.get("description", ""),
            adequately_mitigated=bool(r.get("adequately_mitigated", False)),
            concern=r.get("concern", ""),
        )
        for r in data.get("risk_assessment", [])
    ]

    dependency_assessment = [
        DependencyAssessment(
            description=d.get("description", ""),
            validated=bool(d.get("validated", False)),
            concern=d.get("concern", ""),
        )
        for d in data.get("dependency_assessment", [])
    ]

    return EngineeringReadinessReport(
        goal=goal,
        executive_summary=data.get("executive_summary", ""),
        readiness_status=data.get("readiness_status", ""),
        completeness_findings=completeness_findings,
        repository_review=data.get("repository_review", []),
        component_review=data.get("component_review", []),
        risk_assessment=risk_assessment,
        dependency_assessment=dependency_assessment,
        test_strategy_review=data.get("test_strategy_review", []),
        blocking_issues=data.get("blocking_issues", []),
        recommendations=data.get("recommendations", []),
        prompt_version=_PROMPT_VERSION,
    )


# ---------------------------------------------------------------------------
# Engineering Review Agent
# ---------------------------------------------------------------------------


class EngineeringReviewAgent:
    """Implements IAgent for goal=review_readiness.

    Stateless singleton, same pattern as every other agent — no db/graph
    session needed since this agent runs no tools.
    """

    async def run(self, context: AgentContext) -> AgentOutput:
        subject_id: str = context.subject.subject_id
        workflow = context.extras.get("workflow")

        planning_result = get_stage_result(workflow, "planning") if workflow else None
        development_result = get_stage_result(workflow, "development") if workflow else None
        testing_result = get_stage_result(workflow, "testing") if workflow else None

        blueprint_context = _build_blueprint_context(
            context.subject.display_name, planning_result, development_result, testing_result
        )

        logger.info(
            "engineering_review_agent_started subject_id=%s context_chars=%d "
            "planning=%s development=%s testing=%s model=%s",
            subject_id,
            len(blueprint_context),
            planning_result is not None,
            development_result is not None,
            testing_result is not None,
            context.model,
        )

        missing = [
            label
            for label, result in (
                ("Planning", planning_result),
                ("Development", development_result),
                ("Testing", testing_result),
            )
            if result is None
        ]
        evidence: list[Evidence] = [
            Evidence(
                kind="tool_call",
                reference="read_workflow_context",
                summary=(
                    "Read full structured results from the Planning, Development, and "
                    "Testing stages via get_stage_result() — no summarization or "
                    "truncation applied." + (f" Missing: {', '.join(missing)}." if missing else "")
                ),
            )
        ]

        prompt = _render_prompt(blueprint_context)

        try:
            raw_response = await _call_llm(user_prompt=prompt, model=context.model)
            report = _parse_llm_response(raw_response, context.goal)
        except EngineeringReviewLLMError as exc:
            logger.error("engineering_review_agent_llm_failed error=%s", str(exc))
            raise

        evidence.append(
            Evidence(
                kind="llm_reasoning",
                reference="llm_synthesis",
                summary=(
                    f"Readiness assessment: {report.readiness_status or 'unknown'} — "
                    f"{len(report.blocking_issues)} blocking issue(s), "
                    f"{len(report.completeness_findings)} completeness finding(s)."
                ),
            )
        )

        confidence_score = _READINESS_CONFIDENCE.get(report.readiness_status, 0.5)
        confidence_reasoning = (
            f"Readiness status '{report.readiness_status or 'unknown'}' derived from "
            f"{len(report.completeness_findings)} completeness finding(s), "
            f"{len(report.risk_assessment)} risk assessment(s), and "
            f"{len(report.dependency_assessment)} dependency assessment(s) "
            f"against the Planning/Development/Testing blueprint."
        )

        logger.info(
            "engineering_review_agent_completed subject_id=%s readiness=%s "
            "blocking_issues=%d confidence=%.2f",
            subject_id,
            report.readiness_status,
            len(report.blocking_issues),
            confidence_score,
        )

        return AgentOutput(
            agent_id="engineering_review",
            subject_id=subject_id,
            confidence=Confidence(score=confidence_score, reasoning=confidence_reasoning),
            evidence=evidence,
            result=report.model_dump(),
            prompt_version=_PROMPT_VERSION,
        )
