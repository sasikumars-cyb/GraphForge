"""Deterministic confidence calculation for the Code Generation Agent.

The LLM's own `confidence` field (schemas.GeneratedCodeResult once
included this) is model opinion — nothing checks it against anything, so
it is dropped entirely rather than read. `calculate_confidence` computes a
score purely from verification evidence this run itself gathered: whether
the repository is tracked, in scope for the workflow, whether prior-stage
graph context was available, and whether the generated file operations
passed validation. Same evidence for every provider/model — nothing here
depends on what the LLM said about itself.

The actual weighted-sum arithmetic lives in the shared
`app.agents.confidence` engine (every agent's evidence-based confidence
funnels through the same calculator now) — this module owns only the
domain-specific pieces: which flags exist, what they weigh, and the
per-file-violation penalty.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.agents.confidence import WeightedEvidence, calculate_weighted_confidence

# Each flag contributes its weight when True; weights sum to 1.0 so a
# fully-verified run with no file violations scores 1.0.
_WEIGHTS: dict[str, float] = {
    "repository_tracked": 0.20,
    "repository_in_workflow_scope": 0.30,
    "graph_context_available": 0.20,
    "previous_stage_verified": 0.10,
    "files_valid": 0.20,
}

# Per-violation confidence penalty, capped so a handful of bad file
# operations can't push the score below the sum of failed gates already
# reflected in `files_valid=False`.
_FILE_VIOLATION_PENALTY = 0.1
_MAX_FILE_PENALTY = 0.4


@dataclass(frozen=True)
class ConfidenceEvidence:
    """Deterministic facts this run verified — the only inputs to
    `calculate_confidence`. Nothing here is an LLM claim."""

    repository_tracked: bool = False
    repository_in_workflow_scope: bool = False
    graph_context_available: bool = False
    previous_stage_verified: bool = False
    files_valid: bool = True
    file_violation_count: int = 0


def calculate_confidence(evidence: ConfidenceEvidence) -> tuple[float, str]:
    """Return (score, reasoning) via the shared weighted-evidence engine
    (app.agents.confidence.calculate_weighted_confidence)."""
    penalty = 0.0
    penalty_reason = ""
    if evidence.file_violation_count:
        penalty = min(_MAX_FILE_PENALTY, _FILE_VIOLATION_PENALTY * evidence.file_violation_count)
        penalty_reason = f"file_violations={evidence.file_violation_count}"

    flags = {name: bool(getattr(evidence, name)) for name in _WEIGHTS}
    return calculate_weighted_confidence(
        WeightedEvidence(
            flags=flags,
            weights=_WEIGHTS,
            penalty=penalty,
            penalty_reason=penalty_reason,
        )
    )
