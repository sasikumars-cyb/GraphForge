"""Renders a stored `AIAnalysisResult` (never a freshly-run one) plus the
deterministic risk/impact result as a GitHub-flavored markdown PR comment,
for `POST /pull-requests/{id}/publish-review`.

Pure formatting only - no I/O, no DB, no HTTP, no knowledge of GitHub's
API shape. `directly_impacted_services`/`indirectly_impacted_services` are
raw JSON dicts shaped like `app.analysis.models.impact.ImpactedNode`
(id/name/node_type/repository_id, all strings) - exactly what's persisted
in `PullRequestAnalysis.directly_impacted_services`/`indirectly_impacted_services`
(`Mapped[list[dict[str, str]]]`).
"""

from __future__ import annotations

from app.ai.schemas.analysis_result import AIAnalysisResult

_NONE = "None."


def _confidence_pct(score: float) -> int:
    return round(score * 100)


def _impacted_service_names(nodes: list[dict[str, str]]) -> str:
    if not nodes:
        return _NONE
    return ", ".join(str(node["name"]) for node in nodes)


def _breaking_changes_section(result: AIAnalysisResult) -> list[str]:
    if not result.breaking_changes:
        return ["## Breaking Changes", "", "No breaking changes identified.", ""]
    lines = ["## Breaking Changes", ""]
    for bc in result.breaking_changes:
        lines.append(
            f"- **{bc.component}** ({bc.severity}): {bc.description} "
            f"_(confidence: {_confidence_pct(bc.confidence.score)}%)_"
        )
    lines.append("")
    return lines


def _migration_advice_section(result: AIAnalysisResult) -> list[str]:
    if not result.migration_advice:
        return ["## Migration Advice", "", "No migration advice provided.", ""]
    lines = ["## Migration Advice", ""]
    for advice in result.migration_advice:
        lines.append(f"- **{advice.component}** ({advice.priority} priority): {advice.advice}")
    lines.append("")
    return lines


def _impacted_services_section(
    directly_impacted_services: list[dict[str, str]],
    indirectly_impacted_services: list[dict[str, str]],
) -> list[str]:
    return [
        "## Impacted Services",
        "",
        f"**Directly impacted:** {_impacted_service_names(directly_impacted_services)}",
        "**Indirectly impacted (cross-repository):** "
        f"{_impacted_service_names(indirectly_impacted_services)}",
        "",
    ]


def _suggested_reviewers_section(result: AIAnalysisResult) -> list[str]:
    if not result.suggested_reviewers:
        return ["## Suggested Reviewers", "", "No reviewers suggested.", ""]
    lines = ["## Suggested Reviewers", ""]
    for reviewer in result.suggested_reviewers:
        lines.append(
            f"- @{reviewer.reviewer} — {reviewer.reason} "
            f"_(confidence: {_confidence_pct(reviewer.confidence.score)}%)_"
        )
    lines.append("")
    return lines


def _regression_tests_section(result: AIAnalysisResult) -> list[str]:
    if not result.regression_tests:
        return ["## Recommended Regression Tests", "", "No regression tests suggested.", ""]
    lines = ["## Recommended Regression Tests", ""]
    for test in result.regression_tests:
        lines.append(f"- **{test.component}** ({test.priority} priority): {test.test_description}")
    lines.append("")
    return lines


def _release_plan_section(result: AIAnalysisResult) -> list[str]:
    plan = result.release_coordination_plan
    lines = ["## Release Plan", ""]

    lines.append("**Deployment order:**")
    if plan.deployment_order:
        for step in plan.deployment_order:
            lines.append(f"{step.order}. **{step.repository}** — {step.action} _({step.reason})_")
    else:
        lines.append("No deployment order needed - single repository change.")
    lines.append("")

    lines.append("**Repositories to notify:**")
    if plan.repositories_to_notify:
        for entry in plan.repositories_to_notify:
            marker = "🔴 blocking" if entry.urgency == "blocking" else "🟡 advisory"
            lines.append(f"- **{entry.repository}** ({marker}): {entry.reason}")
    else:
        lines.append(_NONE)
    lines.append("")

    lines.append(f"**Rollout strategy:** {plan.rollout_strategy or 'Not specified.'}")
    lines.append(
        f"**Backward compatibility:** {plan.backward_compatibility_advice or 'Not specified.'}"
    )
    lines.append(f"**Communication summary:** {plan.communication_summary or 'Not specified.'}")
    lines.append("")

    lines.append("**Rollout risks:**")
    if plan.rollout_risks:
        lines.extend(f"- {risk}" for risk in plan.rollout_risks)
    else:
        lines.append("None identified.")
    lines.append("")

    return lines


def format_review_comment(
    ai_result: AIAnalysisResult,
    risk: str,
    directly_impacted_services: list[dict[str, str]],
    indirectly_impacted_services: list[dict[str, str]],
) -> str:
    """Builds the full markdown comment body. Never calls the LLM, never
    touches the network - `ai_result` must already be a fully-computed,
    persisted analysis; `risk`/the impacted-service lists come from the
    deterministic engine's own persisted result (or `"UNKNOWN"`/empty
    lists when no deterministic analysis has been run yet)."""
    lines = ["# 🤖 ChangeGuard AI Review", "", "## Summary", ""]
    lines += [ai_result.executive_summary or _NONE, ""]
    lines += ["## Risk", "", f"**{risk}**", ""]
    lines += _breaking_changes_section(ai_result)
    lines += _migration_advice_section(ai_result)
    lines += _impacted_services_section(directly_impacted_services, indirectly_impacted_services)
    lines += _suggested_reviewers_section(ai_result)
    lines += _regression_tests_section(ai_result)
    lines += _release_plan_section(ai_result)
    lines += [
        "---",
        f"Generated by ChangeGuard AI · prompt `{ai_result.prompt_version}` · "
        f"confidence {_confidence_pct(ai_result.confidence.score)}% · "
        "this review was published from a previously computed analysis, not a new LLM call.",
    ]
    return "\n".join(lines)
