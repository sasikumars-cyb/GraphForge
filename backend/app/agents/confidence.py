"""Shared, evidence-based confidence framework.

Every agent in this codebase computes confidence deterministically from
its own run's verification evidence — never from an LLM's self-reported
opinion (confirmed across the whole agent inventory: none of Planning,
Development, Testing, Documentation Planning, or Engineering Review ever
parse an LLM-reported `confidence` field; Code Generation used to, and
`app.agents.code_generation.verification` + `.confidence` closed that gap).
What differed per agent was the *mechanics* of turning evidence into a
single `[0, 1]` score:

- Code Generation: a named-flag dataclass + a fixed weight table.
- Planning / Development / Testing: a base score derived from a
  graph-availability tri-state (unavailable / partial / full data), plus
  a small structural bonus.

This module factors out the piece that generalizes across all of them —
weighted sum of named boolean evidence flags, capped to `[0, 1]`, with an
auditable reasoning string — so a new agent (or a refactor of an existing
one) never has to re-derive that arithmetic.

Deliberately NOT centralized: the actual weights, and which flags exist.
Planning's "0.25 unavailable / 0.40 partial / 0.85 full, +0.05 structural
bonus" encodes that agent's own domain judgment about what graph grounding
is worth for a *plan*; Code Generation's weights encode a different
judgment about what proves a *git operation* is safe to run. Forcing every
agent onto one shared weight table would either change existing confidence
numbers (forbidden — see "Preserve Existing Behaviour" in the LLM
architecture deliverable) or require a superset of flags meaningless to
most agents. The shared piece is the calculator; the evidence and its
weights stay agent-owned — the same split `app.agents.verification`
already uses (centralized claim-matching, agent-owned choice of which
claims to check).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class WeightedEvidence:
    """A named set of boolean facts this run verified, plus the weight
    each contributes when true, and an optional flat penalty (e.g. "N
    violations found") applied after the weighted sum."""

    flags: Mapping[str, bool]
    weights: Mapping[str, float]
    penalty: float = 0.0
    penalty_reason: str = ""


def calculate_weighted_confidence(evidence: WeightedEvidence) -> tuple[float, str]:
    """Weighted sum of `evidence.flags` under `evidence.weights`, minus
    `evidence.penalty`, clamped to `[0, 1]`. Returns `(score, reasoning)`
    — `reasoning` lists every flag considered plus any penalty applied, so
    the number is auditable rather than a black box.

    Flags with no matching entry in `weights` are ignored (not an error) —
    callers pass exactly the weight table for the flags they care about.
    """
    score = 0.0
    parts: list[str] = []
    for name, weight in evidence.weights.items():
        value = bool(evidence.flags.get(name, False))
        if value:
            score += weight
        parts.append(f"{name}={value}")

    if evidence.penalty:
        score = max(0.0, score - evidence.penalty)
        parts.append(f"{evidence.penalty_reason or 'penalty'} (-{evidence.penalty:.2f})")

    score = round(min(1.0, max(0.0, score)), 2)
    reasoning = (
        f"Deterministic confidence ({score:.2f}) computed from verification evidence, "
        "not model self-assessment: " + ", ".join(parts)
    )
    return score, reasoning
