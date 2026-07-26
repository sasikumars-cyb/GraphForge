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

The Development and Testing agents now read their own one prior stage the
same way (see app.agents.stage_context, extracted from here) — this agent
is just the one that needed all three at once, since it runs no tools of
its own to re-derive anything from the graph directly.

Reviews planning artifacts — implementation completeness, repository/
component selection, risks, dependencies, test strategy. Never a git
diff; that is the separate, unchanged Review Agent (review_pr), which
this agent does not call and which never runs inside a Planning workflow.
"""

from __future__ import annotations

import json
import logging
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
from app.agents.stage_context import (
    format_development_block as _format_development_block,
    format_planning_block as _format_planning_block,
    format_testing_block as _format_testing_block,
)
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
