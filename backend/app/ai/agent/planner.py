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
    just recording a bare true/false."""

    should_call: bool
    reasoning: str


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
