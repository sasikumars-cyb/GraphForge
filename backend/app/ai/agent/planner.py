"""Rule-based tool-selection logic for the Change Investigation Agent.

Deliberately **not** an LLM call: every decision below is a plain,
auditable rule over already-known state (risk level, whether traversal
found anything, whether cross-repository impact exists). This keeps the
loop deterministic, free, and instant, and reserves the one LLM call in
the whole investigation for what genuinely needs judgment - synthesizing
the final analysis. See `investigation_agent.InvestigationAgent` for how
each decision maps to a step in the reasoning log.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.analysis.models.impact import RiskLevel


@dataclass(frozen=True)
class PlannerDecision:
    """Whether to call a tool, and the plain-language reason why - the
    reason is what makes the reasoning log explain itself rather than
    just recording a bare true/false. `tool_name` is only set by decisions
    that must name *which* tool among several candidates (currently only
    `should_retry_after_low_confidence`) - every other decision point has
    exactly one tool in mind already, so it's implicit there."""

    should_call: bool
    reasoning: str
    tool_name: str | None = None


class AgentPlanner:
    """One method per decision point in the investigation loop. Each
    returns a `PlannerDecision` rather than mutating anything, so it stays
    trivially testable in isolation from the tools/state it's deciding
    about."""

    def should_traverse_graph(self, *, has_direct_nodes: bool) -> PlannerDecision:
        if not has_direct_nodes:
            return PlannerDecision(
                should_call=False,
                reasoning=(
                    "No changed file matched an indexed graph node - nothing to traverse "
                    "from. Likely a docs/config/DTO-only change."
                ),
            )
        return PlannerDecision(
            should_call=True,
            reasoning=(
                "Changed files matched indexed graph node(s) - expanding to downstream impact."
            ),
        )

    def should_check_indexing_information(self, *, has_direct_nodes: bool) -> PlannerDecision:
        if has_direct_nodes:
            return PlannerDecision(
                should_call=False,
                reasoning="Direct nodes were found, so the index is clearly current for this path.",
            )
        return PlannerDecision(
            should_call=True,
            reasoning=(
                "Zero direct nodes matched - checking the index's own node counts to confirm "
                "this is a genuinely non-architectural change rather than a stale/sparse index."
            ),
        )

    def should_retrieve_repository_metadata(
        self, *, has_cross_repository_impact: bool
    ) -> PlannerDecision:
        if not has_cross_repository_impact:
            return PlannerDecision(
                should_call=False,
                reasoning=(
                    "No cross-repository peers found - nothing beyond the current "
                    "repository to resolve."
                ),
            )
        return PlannerDecision(
            should_call=True,
            reasoning=(
                "Cross-repository impact found - resolving repository names for the "
                "coordination plan."
            ),
        )

    def should_read_diff(self, *, risk: RiskLevel) -> PlannerDecision:
        if risk == RiskLevel.LOW:
            return PlannerDecision(
                should_call=False,
                reasoning="Risk is LOW - the node/dependency summary is already enough context.",
            )
        return PlannerDecision(
            should_call=True,
            reasoning=(
                f"Risk is {risk.value} - fetching the actual diff to ground "
                "breaking-change analysis."
            ),
        )

    def should_read_git_history(self, *, has_impacted_services: bool) -> PlannerDecision:
        if not has_impacted_services:
            return PlannerDecision(
                should_call=False,
                reasoning="No service is impacted - there is no reviewer suggestion to ground.",
            )
        return PlannerDecision(
            should_call=True,
            reasoning=(
                "Service impact found - fetching real commit authorship to ground "
                "reviewer suggestions."
            ),
        )

    def should_retry_after_low_confidence(
        self,
        *,
        confidence_score: float,
        has_diff: bool,
        has_authors: bool,
        has_impacted_services: bool,
        has_impacted_repositories: bool,
        has_cross_repository_peers: bool,
    ) -> PlannerDecision:
        """Whether the agent should react to its own low-confidence result
        by gathering exactly one more piece of evidence before re-asking
        the LLM. Priority order (first eligible candidate wins): diff,
        then git history, then repository metadata - each gated on both
        "not already gathered" and the same precondition its own planner
        method already requires, so this never re-fetches something
        pointless just because confidence happened to be low. Called at
        most once per investigation - the caller enforces the hard cap,
        not this method."""
        if confidence_score >= 0.5:
            return PlannerDecision(
                should_call=False, reasoning="Confidence is sufficient - no retry needed."
            )
        if not has_diff:
            return PlannerDecision(
                should_call=True,
                reasoning=(
                    f"Confidence ({confidence_score:.2f}) is below 0.5 and no diff was read "
                    "yet - fetching it to ground the retry."
                ),
                tool_name="read_git_diff",
            )
        if not has_authors and has_impacted_services:
            return PlannerDecision(
                should_call=True,
                reasoning=(
                    f"Confidence ({confidence_score:.2f}) is below 0.5 and no authorship was "
                    "gathered yet - fetching git history to ground the retry."
                ),
                tool_name="read_git_history",
            )
        if not has_impacted_repositories and has_cross_repository_peers:
            return PlannerDecision(
                should_call=True,
                reasoning=(
                    f"Confidence ({confidence_score:.2f}) is below 0.5 and cross-repository "
                    "impact is still unresolved - retrieving repository metadata."
                ),
                tool_name="retrieve_repository_metadata",
            )
        return PlannerDecision(
            should_call=False,
            reasoning=(
                f"Confidence ({confidence_score:.2f}) is below 0.5 but no further evidence "
                "exists worth gathering."
            ),
        )
