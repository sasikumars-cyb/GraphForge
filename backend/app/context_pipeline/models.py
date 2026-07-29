"""Data model for the Context Resolution Pipeline.

`Reference` and `ResolvedArtifact` are what a provider produces;
`EnrichedPlanningRequest` is what the Planning Agent consumes. The
Planning Agent only ever sees `EnrichedPlanningRequest` — it has no way
to tell whether an artifact came from Jira, Confluence, GitHub, the
Knowledge Graph, or a provider that doesn't exist yet.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from app.agents._contract import Evidence
from app.agents.planning.classifier import PlanningProfile


class ReferenceType(StrEnum):
    """What kind of external reference was detected in the raw prompt."""

    JIRA_ISSUE = "jira_issue"
    CONFLUENCE_PAGE = "confluence_page"
    GITHUB_REPOSITORY = "github_repository"
    GITHUB_ISSUE = "github_issue"
    GITHUB_PULL_REQUEST = "github_pull_request"
    GITHUB_FILE = "github_file"
    LOCAL_REPOSITORY = "local_repository"


class ProviderCapability(StrEnum):
    """What a registered provider is able to resolve. A provider declares
    one or more of these; the pipeline asks "which registered provider
    can resolve this reference/need" by capability, never by a
    hardcoded provider name."""

    ISSUE_TRACKER = "issue_tracker"
    DOCUMENTATION = "documentation"
    SOURCE_CONTROL = "source_control"
    REPOSITORY_METADATA = "repository_metadata"
    KNOWLEDGE_SOURCE = "knowledge_source"
    GRAPH = "graph"


@dataclass(frozen=True)
class Reference:
    """A structured, deterministically-detected reference to something
    external — a Jira key, a GitHub PR URL, and so on. Detection never
    touches the network; it only recognizes a shape in text (see
    `reference_detection.detect_references`)."""

    type: ReferenceType
    provider: str
    confidence: float
    raw_value: str
    normalized_value: str


@dataclass
class ResolvedArtifact:
    """One normalized piece of retrieved context, regardless of which
    provider or transport produced it. `text` is what's safe to fold
    into the planning prompt (already wrapped as untrusted content and
    secret-redacted by the provider that produced it); `evidence` is
    the contract-shaped record of the retrieval itself."""

    provider: str
    capability: ProviderCapability
    reference: Reference | None
    title: str
    text: str
    evidence: Evidence
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class AdditionalContextRecommendation:
    """Phase 6 (LLM-assisted discovery) output: a decision about whether
    more context should be sought, and from where — never a retrieval
    itself. The pipeline is the only thing that ever calls a provider;
    this is just a recommendation an operator or a future pipeline pass
    can act on."""

    should_search: bool
    capability: ProviderCapability | None
    reasoning: str


@dataclass
class EnrichedPlanningRequest:
    """The only thing the Planning Agent consumes. Everything the agent
    used to derive itself by parsing the prompt and dispatching tools —
    Jira/Confluence/GitHub enrichment, capability classification, graph
    retrieval — is already resolved and normalized here.
    """

    # The literal text the user typed/pasted — never modified, so the UI's
    # "Task Description" field keeps showing exactly that.
    original_request: str

    # original_request plus every resolved artifact's text, each wrapped as
    # untrusted content — this is what the planning prompt is rendered from.
    enriched_text: str

    resolved_references: list[Reference]
    artifacts: list[ResolvedArtifact]

    # Capability classification of the enriched text — deterministic,
    # keyword-driven (see app.agents.planning.classifier). Drives both the
    # prompt template choice and the graph retrieval's relevance ranking,
    # so it belongs to context resolution, not to reasoning over the result.
    profile: PlanningProfile

    # Knowledge Graph retrieval — normalized, structured, and already
    # ranked/filtered by the resolved profile's search terms.
    indexed_repositories: list[dict[str, Any]]
    graph_components: list[dict[str, Any]]
    graph_topics: list[dict[str, Any]]
    ranked_repository_names: list[str]
    graph_context_text: str
    graph_available: bool
    graph_has_data: bool

    additional_context_recommendation: AdditionalContextRecommendation | None

    # Every retrieval this pipeline performed, contract-shaped — the
    # Planning Agent appends its own reasoning/verification evidence to
    # this same list, it never has to reconstruct it.
    evidence: list[Evidence]

    # Free-form bookkeeping (which references were detected and by which
    # provider, whether discovery ran, etc.) — for logging/diagnostics,
    # not consumed by the LLM prompt.
    planning_metadata: dict[str, Any] = field(default_factory=dict)
