"""ContextDiscoveryResult — the JSON-serializable shape persisted into
AgentStep.result for the context_discovery stage.

Deliberately a near-mirror of `app.context_pipeline.models.
EnrichedPlanningRequest`, minus the two fields that don't belong in a
persisted JSON blob:

- `profile` (a `PlanningProfile` dataclass referencing shared, static
  `Capability`/`ArchitecturePattern` singletons) is not serialized here at
  all. `analyse()` (app.agents.planning.classifier) is a pure, cheap,
  deterministic function of `enriched_text` alone — no I/O, no LLM call —
  so any consumer that needs the profile (Planning) re-derives it from
  the persisted `enriched_text` rather than this agent shipping a
  bespoke serialize/deserialize path for a dataclass full of shared
  object references.
- `artifacts` (the raw `ResolvedArtifact` objects) aren't persisted
  separately — each artifact's text is already folded into
  `enriched_text`, and its retrieval is already recorded as an `Evidence`
  entry on the `AgentOutput` this schema rides inside of. Persisting a
  third copy would be exactly the kind of duplicated storage the
  Context Explorer architecture review's "reuse AgentStep, don't
  duplicate" conclusion was about avoiding.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ContextDiscoveryResult(BaseModel):
    """What Context Discovery produces — persisted verbatim into
    AgentStep.result. Planning (and anything else downstream) reads this
    back via `get_stage_result()`."""

    # The literal text the user typed/pasted — never modified. Kept
    # alongside `enriched_text` for the same reason PlanningResult keeps
    # `task_description` next to its enriched prompt: so a reviewer can
    # always see what the user actually asked for.
    original_request: str

    # `original_request` plus every resolved artifact's text, each already
    # wrapped as untrusted content by the provider that produced it — this
    # is the text Planning's prompt is rendered from.
    enriched_text: str

    # Reference dataclass fields, flattened to plain dicts: type,
    # provider, confidence, raw_value, normalized_value.
    resolved_references: list[dict[str, Any]] = Field(default_factory=list)

    # Knowledge Graph retrieval — normalized, structured, already ranked
    # by the classifier's search terms.
    indexed_repositories: list[dict[str, Any]] = Field(default_factory=list)
    graph_components: list[dict[str, Any]] = Field(default_factory=list)
    graph_topics: list[dict[str, Any]] = Field(default_factory=list)
    ranked_repository_names: list[str] = Field(default_factory=list)
    graph_context_text: str = ""
    graph_available: bool = False
    graph_has_data: bool = False

    # Phase 6 (LLM-assisted discovery) output, when it ran — a
    # recommendation only, never itself a retrieval. See
    # app.context_pipeline.discovery's module docstring.
    additional_context_recommendation: dict[str, Any] | None = None

    # Free-form bookkeeping — which reference types were detected, whether
    # discovery ran, etc. — for diagnostics, not consumed by any prompt.
    planning_metadata: dict[str, Any] = Field(default_factory=dict)

    prompt_version: str = "1.0"
