"""Documentation Planning Agent — Documentation Planning capability.

Implements the IAgent protocol for goal=plan_documentation. Like
Engineering Review, this agent runs no graph tools of its own — it
synthesizes over the prior three stages' structured results (Planning,
Development, Testing), read via get_stage_result() (the untruncated
AgentStep.result JSON — see app.agents.git_ops._artifact_reader and
app.agents.stage_context), not via workflow_service.build_stage_context()/
resolve_freetext()'s 256-char-truncated summary chain.

This is a planning stage, not a documentation generator: it determines
which documentation should be created, updated, or left unchanged once
implementation is complete — ownership, priority, effort, and dependencies
per item — and never produces documentation prose itself.

Positioned between Testing and Engineering Review in the "planning"
workflow type (see workflow_service.WORKFLOW_TYPE_STAGES) so Engineering
Review can, in turn, read this stage's own result the same way Planning/
Development/Testing already read each other's.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.agents._contract import (
    AgentContext,
    AgentOutput,
    Confidence,
    Evidence,
)
from app.agents.documentation_planning.schemas import (
    DocumentationChecklistItem,
    DocumentationPlan,
    DocumentationRisk,
    ExistingDocumentationUpdate,
    NewDocumentationItem,
    RequiredDocumentationUpdate,
)
from app.agents.git_ops._artifact_reader import get_stage_result
from app.agents.llm import STAGE_DOCUMENTATION_PLANNING, invoke_llm_json, stage_for
from app.agents.prompt_utils import parse_json_response, render_prompt_template
from app.agents.stage_context import (
    format_development_block as _format_development_block,
)
from app.agents.stage_context import (
    format_planning_block as _format_planning_block,
)
from app.agents.stage_context import (
    format_testing_block as _format_testing_block,
)
from app.core.exceptions import AppError

logger = logging.getLogger(__name__)

_PROMPT_VERSION = "1.0"
_PROMPT_DIR = Path(__file__).parent / "prompts"
_MAX_CONTEXT_CHARS = 8_000

# Three stages' worth of full structured artifacts — same rationale as
# engineering_review/agent.py's identical constant: this agent has no
# graph_context of its own, so this is the only real cap on prompt size.
_MAX_BLUEPRINT_CONTEXT_CHARS = 24_000

_IMPACT_CONFIDENCE = {
    "high": 0.85,
    "medium": 0.75,
    "low": 0.7,
    "none": 0.6,
}
_DEFAULT_CONFIDENCE = 0.5


def _collect_verification_warnings(
    planning_result: dict[str, Any] | None,
    development_result: dict[str, Any] | None,
    testing_result: dict[str, Any] | None,
) -> list[str]:
    """Pull each stage's own deterministic `verification_warnings` — never
    re-derived or judged by this agent's LLM call, just read straight out
    of the stored stage results. Same pattern as
    engineering_review/agent.py's identically-named helper."""
    warnings: list[str] = []
    for label, result in (
        ("Planning", planning_result),
        ("Development", development_result),
        ("Testing", testing_result),
    ):
        for w in (result or {}).get("verification_warnings", []) or []:
            warnings.append(f"[{label}] {w}")
    return warnings


def _build_blueprint_context(
    original_objective: str,
    planning_result: dict[str, Any] | None,
    development_result: dict[str, Any] | None,
    testing_result: dict[str, Any] | None,
    verification_warnings: list[str],
) -> str:
    sections = [
        f"## Original Objective\n{original_objective}",
        _format_planning_block(planning_result),
        _format_development_block(development_result),
        _format_testing_block(testing_result),
    ]
    if verification_warnings:
        sections.append(
            "## Pre-existing Verification Warnings (deterministic, not LLM-generated)\n"
            "These were flagged by each stage's own code against its own tool evidence "
            "before this stage ever ran — weigh them as real, already-established facts, "
            "not claims to re-judge:\n" + "\n".join(f"- {w}" for w in verification_warnings)
        )
    context = "\n\n".join(sections)
    if len(context) > _MAX_BLUEPRINT_CONTEXT_CHARS:
        logger.warning(
            "documentation_planning_blueprint_context_truncated original_chars=%d max_chars=%d",
            len(context),
            _MAX_BLUEPRINT_CONTEXT_CHARS,
        )
        context = context[:_MAX_BLUEPRINT_CONTEXT_CHARS]
    return context


# ---------------------------------------------------------------------------
# LLM call — documentation-planning-specific, same mechanics as Planning/
# Development/Testing/Engineering Review (via app.agents.llm's
# StageAwareLLMProvider), separate error class per the existing per-agent
# convention.
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = (
    "You are a Principal Engineer acting as a Documentation Planning Agent. "
    "Respond ONLY with valid JSON matching the requested schema. "
    "Do not include markdown fences or commentary outside the JSON object."
)


class DocumentationPlanningLLMError(AppError):
    status_code = 502
    error_code = "documentation_planning_llm_error"


async def _call_llm(
    user_prompt: str, model: str | None = None, stage: str = STAGE_DOCUMENTATION_PLANNING
) -> str:
    """Delegates to the shared `app.agents.llm.invoke_llm_json` — kept as a
    module-level function so existing test seams
    (`patch("...agent._call_llm", ...)`) stay stable."""
    return await invoke_llm_json(
        system_prompt=_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        stage=stage,
        model=model,
        error_cls=DocumentationPlanningLLMError,
    )


def _render_prompt(blueprint_context: str) -> str:
    """Render documentation_planning.md — this agent has no graph_context
    of its own, so only {{ task_description }} (the full blueprint text
    _build_blueprint_context() assembled) is substituted."""
    return render_prompt_template(
        _PROMPT_DIR / "documentation_planning.md", blueprint_context, "", _MAX_CONTEXT_CHARS
    )


def _parse_llm_response(raw: str, goal: str) -> DocumentationPlan:
    data = parse_json_response(raw, DocumentationPlanningLLMError)

    required_updates = [
        RequiredDocumentationUpdate(
            document=u.get("document", ""),
            category=u.get("category", ""),
            current_status=u.get("current_status", ""),
            action=u.get("action", ""),
            reason=u.get("reason", ""),
            priority=u.get("priority", ""),
            owner=u.get("owner", ""),
            estimated_effort=u.get("estimated_effort", ""),
            dependencies=u.get("dependencies", []),
        )
        for u in data.get("required_updates", [])
    ]

    new_documentation = [
        NewDocumentationItem(
            name=n.get("name", ""),
            category=n.get("category", ""),
            purpose=n.get("purpose", ""),
            suggested_location=n.get("suggested_location", ""),
            owner=n.get("owner", ""),
            priority=n.get("priority", ""),
            estimated_effort=n.get("estimated_effort", ""),
        )
        for n in data.get("new_documentation", [])
    ]

    existing_updates = [
        ExistingDocumentationUpdate(
            file_path=e.get("file_path", ""),
            sections_affected=e.get("sections_affected", []),
            summary_of_changes=e.get("summary_of_changes", ""),
        )
        for e in data.get("existing_updates", [])
    ]

    risks = [
        DocumentationRisk(
            description=r.get("description", ""),
            severity=r.get("severity", ""),
        )
        for r in data.get("risks", [])
    ]

    checklist = [
        DocumentationChecklistItem(
            label=c.get("label", ""),
            applicable=bool(c.get("applicable", True)),
        )
        for c in data.get("checklist", [])
    ]

    return DocumentationPlan(
        goal=goal,
        executive_summary=data.get("executive_summary", ""),
        documentation_impact=data.get("documentation_impact", ""),
        impact_explanation=data.get("impact_explanation", ""),
        required_updates=required_updates,
        new_documentation=new_documentation,
        existing_updates=existing_updates,
        risks=risks,
        recommendations=data.get("recommendations", []),
        release_notes_draft=data.get("release_notes_draft", []),
        checklist=checklist,
        prompt_version=_PROMPT_VERSION,
    )


# ---------------------------------------------------------------------------
# Documentation Planning Agent
# ---------------------------------------------------------------------------


class DocumentationPlanningAgent:
    """Implements IAgent for goal=plan_documentation.

    Stateless singleton, same pattern as every other agent — no db/graph
    session needed since this agent runs no tools of its own.
    """

    async def run(self, context: AgentContext) -> AgentOutput:
        subject_id: str = context.subject.subject_id
        workflow = context.extras.get("workflow")

        planning_result = get_stage_result(workflow, "planning") if workflow else None
        development_result = get_stage_result(workflow, "development") if workflow else None
        testing_result = get_stage_result(workflow, "testing") if workflow else None

        prior_verification_warnings = _collect_verification_warnings(
            planning_result, development_result, testing_result
        )
        blueprint_context = _build_blueprint_context(
            context.subject.display_name,
            planning_result,
            development_result,
            testing_result,
            prior_verification_warnings,
        )

        logger.info(
            "documentation_planning_agent_started subject_id=%s context_chars=%d "
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
        if prior_verification_warnings:
            evidence.append(
                Evidence(
                    kind="tool_call",
                    reference="prior_stage_verification_warnings",
                    summary=(
                        f"Carried forward {len(prior_verification_warnings)} deterministic "
                        "verification warning(s) from Planning/Development/Testing — see "
                        "result.prior_verification_warnings."
                    ),
                )
            )

        prompt = _render_prompt(blueprint_context)

        try:
            raw_response = await _call_llm(
                user_prompt=prompt,
                model=context.model,
                stage=stage_for(context.extras, STAGE_DOCUMENTATION_PLANNING),
            )
            plan = _parse_llm_response(raw_response, context.goal)
        except DocumentationPlanningLLMError as exc:
            logger.error("documentation_planning_agent_llm_failed error=%s", str(exc))
            raise

        plan.prior_verification_warnings = prior_verification_warnings

        evidence.append(
            Evidence(
                kind="llm_reasoning",
                reference="llm_synthesis",
                summary=(
                    f"Documentation impact: {plan.documentation_impact or 'unknown'} — "
                    f"{len(plan.required_updates)} existing document(s) affected, "
                    f"{len(plan.new_documentation)} new document(s) recommended."
                ),
            )
        )

        confidence_score = _IMPACT_CONFIDENCE.get(plan.documentation_impact, _DEFAULT_CONFIDENCE)
        confidence_reasoning = (
            f"Documentation impact '{plan.documentation_impact or 'unknown'}' derived from "
            f"{len(plan.required_updates)} required update(s), "
            f"{len(plan.new_documentation)} new documentation item(s), and "
            f"{len(plan.risks)} documentation risk(s) against the Planning/Development/"
            "Testing blueprint."
        )

        logger.info(
            "documentation_planning_agent_completed subject_id=%s impact=%s "
            "required_updates=%d new_documentation=%d confidence=%.2f",
            subject_id,
            plan.documentation_impact,
            len(plan.required_updates),
            len(plan.new_documentation),
            confidence_score,
        )

        return AgentOutput(
            agent_id="documentation_planning",
            subject_id=subject_id,
            confidence=Confidence(score=confidence_score, reasoning=confidence_reasoning),
            evidence=evidence,
            result=plan.model_dump(),
            prompt_version=_PROMPT_VERSION,
        )
