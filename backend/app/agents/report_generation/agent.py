"""Report Generation Agent — goal=generate_report.

Runs once, when a human approves a Planning workflow's blueprint (see the
dispatch in api/v1/routers/workflows.py's approve_workflow). Reads every
completed stage's full structured result the same untruncated way
Documentation Planning and Engineering Review already do — via
get_stage_result() (app.agents.git_ops._artifact_reader), not via
workflow_service.build_stage_context()'s 256-char-truncated summary chain
— and asks the LLM to synthesize one self-contained HTML report.

This agent produces no new analysis and traverses no graph of its own: it
is a formatting/synthesis layer over what Context Discovery, Planning,
Development, Testing, Documentation Planning, and Engineering Review
already concluded. Persisting the result into the `workflow_reports` table
is the dispatcher's job (see workflows.py's report finalizer), not this
agent's — this agent only returns {"title", "html"} in AgentOutput.result,
same separation of concerns as every other stage finalizer in this
codebase (an agent produces AgentOutput; a dispatch-site on_complete
callback decides what workflow-specific bookkeeping follows from it).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.agents._contract import AgentContext, AgentOutput, Confidence, Evidence
from app.agents.git_ops._artifact_reader import get_stage_result
from app.agents.llm import invoke_llm_json, stage_for
from app.agents.prompt_utils import parse_json_response, render_prompt_template
from app.agents.stage_context import (
    format_development_block,
    format_documentation_block,
    format_engineering_review_block,
    format_planning_block,
    format_repository_relationships_block,
    format_testing_block,
)
from app.core.exceptions import AppError

logger = logging.getLogger(__name__)

_PROMPT_VERSION = "1.0"
_PROMPT_DIR = Path(__file__).parent / "prompts"
_MAX_CONTEXT_CHARS = 8_000

# Every completed stage's full structured artifact, concatenated — same
# rationale as documentation_planning/engineering_review's identical
# constant: this agent has no graph_context of its own, so this is the
# only real cap on prompt size against a workflow with every stage present.
_MAX_WORKFLOW_CONTEXT_CHARS = 32_000

STAGE_GENERATION_STAGE = "report_generation"

# Stage key -> formatter. Only stages that ever appear in the "planning"
# workflow type (the only type that reaches an approval gate — see
# workflow_service.WORKFLOW_TYPE_STAGES/TERMINAL_BEHAVIOR) have an entry;
# a workflow of any other type never reaches approve_workflow, so this
# agent is never dispatched against one.
_STAGE_FORMATTERS: dict[str, Any] = {
    "context_discovery": format_repository_relationships_block,
    "planning": format_planning_block,
    "development": format_development_block,
    "testing": format_testing_block,
    "documentation_planning": format_documentation_block,
    "engineering_review": format_engineering_review_block,
}

# Human-readable order the report should walk stages in, regardless of the
# dict above's definition order.
_STAGE_ORDER = (
    "context_discovery",
    "planning",
    "development",
    "testing",
    "documentation_planning",
    "engineering_review",
)


class ReportGenerationLLMError(AppError):
    status_code = 502
    error_code = "report_generation_llm_error"


_SYSTEM_PROMPT = (
    "You are a Principal Engineer preparing a stakeholder-facing report. "
    "Respond ONLY with valid JSON matching the requested schema. "
    "Do not include markdown fences or commentary outside the JSON object."
)


async def _call_llm(
    user_prompt: str,
    model: str | None,
    stage: str,
    metadata_out: dict[str, Any] | None,
    context: AgentContext,
) -> str:
    """Delegates to the shared `app.agents.llm.invoke_llm_json` — kept as a
    module-level function so tests can patch it the same way every other
    agent's `_call_llm` is patched."""
    return await invoke_llm_json(
        system_prompt=_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        stage=stage,
        model=model,
        error_cls=ReportGenerationLLMError,
        metadata_out=metadata_out,
        context=context,
    )


def _build_workflow_context(original_objective: str, stage_results: dict[str, Any]) -> str:
    sections = [f"## Original Objective\n{original_objective}"]
    for stage in _STAGE_ORDER:
        formatter = _STAGE_FORMATTERS[stage]
        block = formatter(stage_results.get(stage))
        if block:
            sections.append(block)
    context = "\n\n".join(sections)
    if len(context) > _MAX_WORKFLOW_CONTEXT_CHARS:
        logger.warning(
            "report_generation_context_truncated original_chars=%d max_chars=%d",
            len(context),
            _MAX_WORKFLOW_CONTEXT_CHARS,
        )
        context = context[:_MAX_WORKFLOW_CONTEXT_CHARS]
    return context


def _render_prompt(workflow_context: str) -> str:
    return render_prompt_template(
        _PROMPT_DIR / "report_generation.md", workflow_context, "", _MAX_CONTEXT_CHARS
    )


def _parse_llm_response(raw: str) -> tuple[str, str]:
    data = parse_json_response(raw, ReportGenerationLLMError)
    title = str(data.get("title") or "").strip()
    html = str(data.get("html") or "").strip()
    if not html or "<" not in html:
        raise ReportGenerationLLMError("LLM response did not contain a usable HTML document.")
    if not title:
        title = "Workflow Report"
    return title, html


class ReportGenerationAgent:
    """Implements IAgent for goal=generate_report.

    Stateless singleton, same pattern as every other agent — no db/graph
    session needed since this agent runs no tools of its own.
    """

    async def run(self, context: AgentContext) -> AgentOutput:
        subject_id: str = context.subject.subject_id
        workflow = context.extras.get("workflow")

        stage_results: dict[str, Any] = {
            stage: (get_stage_result(workflow, stage) if workflow else None)
            for stage in _STAGE_ORDER
        }
        completed_stages = [s for s in _STAGE_ORDER if stage_results.get(s) is not None]

        logger.info(
            "report_generation_agent_started subject_id=%s completed_stages=%s model=%s",
            subject_id,
            ",".join(completed_stages) or "(none)",
            context.model,
        )

        evidence: list[Evidence] = [
            Evidence(
                kind="tool_call",
                reference="read_workflow_context",
                summary=(
                    f"Read the full structured result of {len(completed_stages)} completed "
                    f"stage(s) via get_stage_result(): {', '.join(completed_stages) or '(none)'}."
                ),
            )
        ]

        workflow_context = _build_workflow_context(context.subject.display_name, stage_results)
        prompt = _render_prompt(workflow_context)

        try:
            llm_metadata: dict[str, Any] = {}
            raw_response = await _call_llm(
                user_prompt=prompt,
                model=context.model,
                stage=stage_for(context.extras, STAGE_GENERATION_STAGE),
                metadata_out=llm_metadata,
                context=context,
            )
            title, html = _parse_llm_response(raw_response)
        except ReportGenerationLLMError as exc:
            logger.error("report_generation_agent_llm_failed error=%s", str(exc))
            raise

        evidence.append(
            Evidence(
                kind="llm_reasoning",
                reference="llm_synthesis",
                summary=f"Synthesized a {len(html)}-character HTML report titled '{title}'.",
            )
        )

        # Confidence tracks completeness, not narrative quality — a report
        # built from every stage is strictly more trustworthy than one
        # built from a partial workflow (which shouldn't normally happen,
        # since this only ever dispatches after approval, but a workflow
        # can still be approved mid-sequence via future stage types).
        total_stages = len(_STAGE_ORDER)
        completeness = len(completed_stages) / total_stages if total_stages else 0.0
        confidence_score = 0.5 + 0.4 * completeness
        confidence_reasoning = (
            f"{len(completed_stages)} of {total_stages} workflow stages had a completed "
            "result available to synthesize from."
        )

        logger.info(
            "report_generation_agent_completed subject_id=%s title=%s html_chars=%d "
            "confidence=%.2f",
            subject_id,
            title,
            len(html),
            confidence_score,
        )

        return AgentOutput(
            agent_id="report_generation",
            subject_id=subject_id,
            confidence=Confidence(score=confidence_score, reasoning=confidence_reasoning),
            evidence=evidence,
            result={"title": title, "html": html},
            prompt_version=_PROMPT_VERSION,
        )
