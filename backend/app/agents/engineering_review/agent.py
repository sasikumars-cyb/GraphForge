"""Engineering Review Agent — Blueprint Readiness capability.

Implements the IAgent protocol for goal=review_readiness. Unlike Planning/
Development/Testing, this agent runs no graph tools of its own — it
synthesizes over the prior four stages' structured results (Planning,
Development, Testing, and Documentation Planning).

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

import logging
from pathlib import Path
from typing import Any

from app.agents import verification
from app.agents._contract import (
    AgentContext,
    AgentOutput,
    Confidence,
    Evidence,
)
from app.agents.confidence import WeightedEvidence, calculate_weighted_confidence
from app.agents.engineering_review.schemas import (
    CompletenessFinding,
    CrossRepositoryImpact,
    DependencyAssessment,
    EngineeringReadinessReport,
    RiskAssessment,
)
from app.agents.git_ops._artifact_reader import get_stage_result
from app.agents.llm import STAGE_ENGINEERING_REVIEW, invoke_llm_json, stage_for
from app.agents.prompt_utils import parse_json_response, render_prompt_template
from app.agents.stage_context import (
    format_development_block as _format_development_block,
)
from app.agents.stage_context import (
    format_documentation_block as _format_documentation_block,
)
from app.agents.stage_context import (
    format_planning_block as _format_planning_block,
)
from app.agents.stage_context import (
    format_repository_relationships_block as _format_repository_relationships_block,
)
from app.agents.stage_context import (
    format_testing_block as _format_testing_block,
)
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

# Readiness-based base confidence, expressed as a one-hot weighted-evidence
# table: exactly one of these three flags is ever true for a given report
# (see _readiness_flags below), so the shared engine's weighted sum reduces
# to "the weight of whichever readiness tier applies" — the same value this
# table produced as a plain dict lookup before this refactor, with the same
# meaning (this agent's own domain judgment about what each tier is worth,
# per app.agents.confidence's module docstring on why weights stay
# agent-owned). Both Code Generation and Documentation Planning already
# route their confidence through this same engine; this table is what makes
# Engineering Review's the third and last, closing the one architectural
# inconsistency the Delta Report flagged (custom arithmetic duplicating
# what the engine already does).
_READINESS_CONFIDENCE = {
    "ready": 0.9,
    "needs_revision": 0.6,
    "not_ready": 0.3,
    # Fallback weight for a readiness_status the LLM returned that isn't one
    # of the three above (schemas.py's readiness_status has no enum
    # constraint — it's a plain `str`, so an empty/unrecognized value is a
    # real, reachable case, not hypothetical). Matches this table's
    # pre-refactor `dict.get(status, 0.5)` default exactly — see
    # _readiness_flags below for how this becomes a one-hot flag like the
    # other three instead of a second code path.
    "_unrecognized": 0.5,
}


def _readiness_flags(readiness_status: str) -> dict[str, bool]:
    """One-hot encode `readiness_status` over `_READINESS_CONFIDENCE`'s
    keys — exactly one flag is true, so `calculate_weighted_confidence`'s
    weighted sum reduces to "the weight of whichever tier applies",
    identical to the plain dict lookup this replaces."""
    key = readiness_status if readiness_status in _READINESS_CONFIDENCE else "_unrecognized"
    return {candidate: candidate == key for candidate in _READINESS_CONFIDENCE}


# Per-warning confidence penalty on top of the readiness-based base above —
# capped so a handful of carried-forward warnings can't push confidence
# below what the categorical tier itself already implies. Passed as
# WeightedEvidence.penalty/penalty_reason (the same mechanism
# code_generation/confidence.py already uses for its own per-violation
# penalty) rather than computed by hand. These two constants stay local to
# this agent — like _READINESS_CONFIDENCE, they encode a domain judgment
# specific to blueprint-readiness review, not a value any other agent
# would share; only the arithmetic that turns them into a score is shared.
_WARNING_PENALTY_PER_ITEM = 0.05
_MAX_WARNING_PENALTY = 0.2


def _collect_verification_findings(
    planning_result: dict[str, Any] | None,
    development_result: dict[str, Any] | None,
    testing_result: dict[str, Any] | None,
    documentation_result: dict[str, Any] | None,
) -> list[dict[str, str | bool]]:
    """Pull each stage's own deterministic, *classified* verification
    findings — never re-derived or judged by this agent's LLM call, just
    read straight out of the stored stage results (see
    app.agents.verification, which both produced them at Planning/
    Development/Testing time AND classified each one's category there).
    This is the ground-truth signal this agent previously had no access
    to: it could only compare the three stages' free text against each
    other, never against what those stages' own tool calls actually
    verified.

    `verification.collect_verification_findings` handles the legacy/
    unmigrated fallback (a stage result with `verification_warnings` but
    no matching `verification_findings` — persisted before this
    classification existed — has every entry treated as blocking, exactly
    as before this classification existed).

    Documentation Planning also carries forward its own
    `prior_verification_findings` (not `verification_findings` — it runs
    no tools of its own either, see documentation_planning/agent.py), so
    those are folded in here too rather than double-read separately;
    deduplicated against what was already collected directly, since
    Documentation Planning computed the exact same Planning/Development/
    Testing set independently.
    """
    findings = verification.collect_verification_findings(
        [
            ("Planning", planning_result),
            ("Development", development_result),
            ("Testing", testing_result),
        ]
    )
    seen = {(f["label"], f["message"]) for f in findings}
    for f in (documentation_result or {}).get("prior_verification_findings", []) or []:
        key = (f.get("label", ""), f.get("message", ""))
        if key not in seen:
            findings.append(
                {
                    "label": str(f.get("label", "")),
                    "message": str(f.get("message", "")),
                    "category": str(f.get("category", "unclassified_legacy")),
                    "blocking": bool(f.get("blocking", True)),
                }
            )
            seen.add(key)
    return findings


def _build_blueprint_context(
    original_objective: str,
    planning_result: dict[str, Any] | None,
    development_result: dict[str, Any] | None,
    testing_result: dict[str, Any] | None,
    documentation_result: dict[str, Any] | None,
    verification_warnings: list[str],
    context_discovery_result: dict[str, Any] | None = None,
) -> str:
    sections = [
        f"## Original Objective\n{original_objective}",
        _format_planning_block(planning_result),
        _format_development_block(development_result),
        _format_testing_block(testing_result),
        _format_documentation_block(documentation_result),
    ]
    if relationships_block := _format_repository_relationships_block(context_discovery_result):
        sections.append(relationships_block)
    if verification_warnings:
        sections.append(
            "## Pre-existing Verification Warnings (deterministic, not LLM-generated)\n"
            "These were flagged by each stage's own code against its own tool evidence "
            "before this review ever ran — weigh them as real, already-established facts, "
            "not claims to re-judge:\n" + "\n".join(f"- {w}" for w in verification_warnings)
        )
    context = "\n\n".join(sections)
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
# Development/Testing/Code Generation (via app.agents.llm's
# StageAwareLLMProvider), separate
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


async def _call_llm(
    user_prompt: str,
    model: str | None = None,
    stage: str = STAGE_ENGINEERING_REVIEW,
    _metadata_out: dict[str, Any] | None = None,
    context: AgentContext | None = None,
) -> str:
    """Delegates to the shared `app.agents.llm.invoke_llm_json` — kept as a
    module-level function so existing test seams
    (`patch("...agent._call_llm", ...)`) stay stable."""
    return await invoke_llm_json(
        system_prompt=_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        stage=stage,
        model=model,
        error_cls=EngineeringReviewLLMError,
        metadata_out=_metadata_out,
        context=context,
    )


def _render_prompt(blueprint_context: str) -> str:
    """Render engineering_review.md — this agent has no graph_context of
    its own, so only {{ task_description }} (the full blueprint text
    _build_blueprint_context() assembled) is substituted."""
    return render_prompt_template(
        _PROMPT_DIR / "engineering_review.md", blueprint_context, "", _MAX_CONTEXT_CHARS
    )


def _graph_derived_repository_facts(
    context_discovery_result: dict[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    """Repository name -> its graph-derived facts (relationship/confidence/
    reason), read from Context Discovery's own canonical `repositories`
    list (ADR 0010 §2's `RepositoryCandidate`, the same structure
    `RepositorySelector.tsx` renders) — never from anything the Engineering
    Review LLM itself claims. Empty when Context Discovery's result is
    absent or predates the canonical `repositories` field (a real prior
    schema, per `RepositoryCandidate`'s own precedent elsewhere in this
    codebase for degrading to "nothing known" rather than guessing)."""
    if not context_discovery_result:
        return {}
    return {
        str(r.get("name", "")): r
        for r in context_discovery_result.get("repositories") or []
        if r.get("name")
    }


def _parse_llm_response(
    raw: str, goal: str, context_discovery_result: dict[str, Any] | None = None
) -> EngineeringReadinessReport:
    data = parse_json_response(raw, EngineeringReviewLLMError)
    repo_facts = _graph_derived_repository_facts(context_discovery_result)

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

    cross_repository_impact = []
    for c in data.get("cross_repository_impact", []):
        repo_name = c.get("repository", "")
        facts = repo_facts.get(repo_name, {})
        cross_repository_impact.append(
            CrossRepositoryImpact(
                repository=repo_name,
                depends_on=c.get("depends_on", []),
                concern=c.get("concern", ""),
                # Graph-derived, not LLM-assessed — see
                # _graph_derived_repository_facts. Empty when the LLM named
                # a repository Context Discovery never suggested (the model
                # is free to reason about anything in the blueprint text;
                # only what the graph actually knows gets backfilled).
                dependency_type=str(facts.get("relationship", "")),
                confidence=str(facts.get("confidence", "")),
                evidence=[str(facts["reason"])] if facts.get("reason") else [],
            )
        )

    return EngineeringReadinessReport(
        goal=goal,
        executive_summary=data.get("executive_summary", ""),
        readiness_status=data.get("readiness_status", ""),
        completeness_findings=completeness_findings,
        repository_review=data.get("repository_review", []),
        component_review=data.get("component_review", []),
        risk_assessment=risk_assessment,
        dependency_assessment=dependency_assessment,
        cross_repository_impact=cross_repository_impact,
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
        documentation_result = (
            get_stage_result(workflow, "documentation_planning") if workflow else None
        )
        # Read-only, same pattern as the four stage reads above — this agent
        # runs no tools, so a repository-relationships section (when Context
        # Discovery found more than one repository in scope) is the only way
        # it ever sees *why* a suggested repository was included, beyond
        # whatever Planning's own `target_repositories` line already named.
        context_discovery_result = (
            get_stage_result(workflow, "context_discovery") if workflow else None
        )

        verification_findings = _collect_verification_findings(
            planning_result, development_result, testing_result, documentation_result
        )
        # Full, label-prefixed text for the prompt/UI/backward-compat field
        # — every finding, blocking or not, so a reviewer can still see
        # informational context. The *decision* below only ever looks at
        # `blocking_findings`.
        prior_verification_warnings = [
            f"[{f['label']}] {f['message']}" for f in verification_findings
        ]
        blocking_findings = [f for f in verification_findings if f["blocking"]]
        blueprint_context = _build_blueprint_context(
            context.subject.display_name,
            planning_result,
            development_result,
            testing_result,
            documentation_result,
            prior_verification_warnings,
            context_discovery_result,
        )

        logger.info(
            "engineering_review_agent_started subject_id=%s context_chars=%d "
            "planning=%s development=%s testing=%s documentation_planning=%s model=%s",
            subject_id,
            len(blueprint_context),
            planning_result is not None,
            development_result is not None,
            testing_result is not None,
            documentation_result is not None,
            context.model,
        )

        missing = [
            label
            for label, result in (
                ("Planning", planning_result),
                ("Development", development_result),
                ("Testing", testing_result),
                ("Documentation Planning", documentation_result),
            )
            if result is None
        ]
        evidence: list[Evidence] = [
            Evidence(
                kind="tool_call",
                reference="read_workflow_context",
                summary=(
                    "Read full structured results from the Planning, Development, Testing, "
                    "and Documentation Planning stages via get_stage_result() — no "
                    "summarization or truncation applied."
                    + (f" Missing: {', '.join(missing)}." if missing else "")
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
            llm_metadata: dict[str, Any] = {}
            raw_response = await _call_llm(
                user_prompt=prompt,
                model=context.model,
                stage=stage_for(context.extras, STAGE_ENGINEERING_REVIEW),
                _metadata_out=llm_metadata,
                context=context,
            )
            report = _parse_llm_response(raw_response, context.goal, context_discovery_result)
        except EngineeringReviewLLMError as exc:
            logger.error("engineering_review_agent_llm_failed error=%s", str(exc))
            raise

        # ------------------------------------------------------------------
        # Ground-truth override: never let the LLM mark a blueprint "ready"
        # over a deterministic, code-verified BLOCKING problem it didn't
        # reconcile. Same pattern as `graph_context_used` elsewhere in this
        # codebase — a real fact from the pipeline itself wins over the
        # model's own verdict, it is never just folded into the prompt and
        # hoped for.
        #
        # Gated on `blocking_findings`, never on "any prior finding is
        # present" — an always-true informational note (e.g. Testing's
        # plan-vs-execution disclaimer) or a false-positive-prone check
        # that already resolved to nothing wrong must never make "ready"
        # permanently unreachable. `VerificationFinding.blocking` is
        # classified at the point each finding was produced (see
        # app.agents.verification), with an explicit fail-closed default —
        # any category this agent has never heard of, or any warning from
        # a stage result persisted before this classification existed,
        # counts as blocking, never silently passed.
        # ------------------------------------------------------------------
        report.prior_verification_warnings = prior_verification_warnings
        report.blocking_verification_warnings = [
            f"[{f['label']}] {f['message']}" for f in blocking_findings
        ]
        if blocking_findings and report.readiness_status == "ready":
            report.readiness_status = "needs_revision"
            report.blocking_issues = list(report.blocking_issues) + [
                "Automatically downgraded from 'ready': one or more prior stages carry "
                "unresolved, code-verified BLOCKING findings (see "
                "blocking_verification_warnings) that this review's own judgment did "
                "not account for."
            ]
            logger.warning(
                "engineering_review_agent_downgraded_ready subject_id=%s "
                "blocking_warning_count=%d total_warning_count=%d",
                subject_id,
                len(blocking_findings),
                len(verification_findings),
            )

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

        # Confidence, via the shared weighted-evidence engine — the same
        # one Code Generation (app.agents.code_generation.confidence) and
        # Documentation Planning (agents/documentation_planning/agent.py)
        # already use. Applied AFTER the deterministic "ready" downgrade
        # above, so a numeric score is never computed from a readiness
        # verdict the override hasn't already had a chance to correct.
        warning_penalty = min(
            _MAX_WARNING_PENALTY, _WARNING_PENALTY_PER_ITEM * len(blocking_findings)
        )
        confidence_score, confidence_reasoning = calculate_weighted_confidence(
            WeightedEvidence(
                flags=_readiness_flags(report.readiness_status),
                weights=_READINESS_CONFIDENCE,
                penalty=warning_penalty,
                penalty_reason=(
                    f"{len(blocking_findings)} carried-forward deterministic blocking "
                    "verification warning(s)"
                    if warning_penalty
                    else ""
                ),
            )
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
