"""Engineering Review Agent — Blueprint Readiness capability.

Implements the IAgent protocol for goal=review_readiness. Unlike Planning/
Development/Testing, this agent runs no graph tools of its own — it
synthesizes over the prior three stages' structured results, which the
Workflow router already folds into a single enriched string via
workflow_service.build_stage_context() before this agent ever sees it
(the exact same cross-stage context mechanism every other stage already
relies on, not a new one built for this agent).

Reviews planning artifacts — implementation completeness, repository/
component selection, risks, dependencies, test strategy. Never a git
diff; that is the separate, unchanged Review Agent (review_pr), which
this agent does not call and which never runs inside a Planning workflow.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import httpx

from app.agents._contract import (
    AgentContext,
    AgentOutput,
    Confidence,
    Evidence,
)
from app.agents._llm import call_chat_completion_json, render_prompt_template
from app.agents.engineering_review.schemas import (
    CompletenessFinding,
    DependencyAssessment,
    EngineeringReadinessReport,
    RiskAssessment,
)
from app.core.exceptions import AppError

logger = logging.getLogger(__name__)

_PROMPT_VERSION = "1.0"
_PROMPT_DIR = Path(__file__).parent / "prompts"
_MAX_CONTEXT_CHARS = 8_000

_READINESS_CONFIDENCE = {
    "ready": 0.9,
    "needs_revision": 0.6,
    "not_ready": 0.3,
}


# ---------------------------------------------------------------------------
# LLM call — engineering-review-specific, same mechanics as Planning/
# Development/Testing (app.agents._llm), separate error class per the
# existing per-agent convention.
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = (
    "You are a Principal Engineer performing a blueprint readiness review. "
    "Respond ONLY with valid JSON matching the requested schema. "
    "Do not include markdown fences or commentary outside the JSON object."
)


class EngineeringReviewLLMError(AppError):
    status_code = 502
    error_code = "engineering_review_llm_error"


async def _call_llm(
    user_prompt: str,
    model: str | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> str:
    return await call_chat_completion_json(
        system_prompt=_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        error_cls=EngineeringReviewLLMError,
        model=model,
        http_client=http_client,
    )


def _render_prompt(blueprint_context: str) -> str:
    """Render engineering_review.md — this agent has no graph_context of
    its own, so only {{ task_description }} (the enriched blueprint text
    build_stage_context() already assembled) is substituted."""
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
        blueprint_context: str = context.subject.display_name
        subject_id: str = context.subject.subject_id

        logger.info(
            "engineering_review_agent_started subject_id=%s context_chars=%d model=%s",
            subject_id,
            len(blueprint_context),
            context.model,
        )

        evidence: list[Evidence] = [
            Evidence(
                kind="tool_call",
                reference="read_workflow_context",
                summary=(
                    f"Reviewed the Planning, Development, and Testing stage summaries "
                    f"({len(blueprint_context)} characters of blueprint context)."
                ),
            )
        ]

        prompt = _render_prompt(blueprint_context)

        try:
            raw_response = await _call_llm(user_prompt=prompt, model=context.model)
            report = _parse_llm_response(raw_response, blueprint_context)
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
