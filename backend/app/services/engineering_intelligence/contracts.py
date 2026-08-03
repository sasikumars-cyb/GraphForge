"""Result shapes returned by the Engineering Intelligence Service Layer.

These are plain, agent-agnostic dataclasses — no `app.agents._contract`
types (`AgentOutput`, `Subject`, `Evidence`) appear here. Translating a
service result into an agent's `AgentOutput` is the calling agent's job,
never this layer's — that boundary is what keeps this package free of
UI/prompt concerns (approved design, "Engineering Intelligence Service
Layer" RFC).

Every collection field is built by the producing service in a
deterministic order (sorted by an explicit key) so two calls against the
same underlying data always return byte-identical results, the same
discipline `app.knowledge_engine.parity` already applies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from app.graph.models import GraphPayload
from app.knowledge_engine.contracts.explanation import ConfidenceExplanation


@dataclass(frozen=True)
class EntityReference:
    """Identifies one graph entity to reason about. `node_id` is a
    `GraphNode.id` (already globally unique/namespaced — see
    `app.graph.models.GraphNode`); `repository_id` is the repository it
    belongs to, required because most lookups are repository-scoped."""

    repository_id: str
    node_id: str


@dataclass(frozen=True)
class RelationshipInsight:
    """One `KnowledgeRelationshipRecord`, reduced to what every service
    in this layer needs from it — confidence state plus its already-
    persisted explanation (never re-derived; see `relationship_lookup`)."""

    relationship_key: str
    relationship_type: str
    source_entity: str
    target_entity: str
    confidence_state: str
    explanation: ConfidenceExplanation | None


@dataclass(frozen=True)
class RepositoryProfile:
    repository_id: str
    apis: tuple[str, ...] = field(default_factory=tuple)
    databases: tuple[str, ...] = field(default_factory=tuple)
    queues: tuple[str, ...] = field(default_factory=tuple)
    integrations: tuple[str, ...] = field(default_factory=tuple)
    dependencies: tuple[str, ...] = field(default_factory=tuple)
    architecture_summary: str = ""


@dataclass(frozen=True)
class BlastRadius:
    seed: EntityReference
    direction: Literal["downstream", "upstream"]
    max_hops: int
    impacted_repositories: tuple[str, ...] = field(default_factory=tuple)
    impacted_apis: tuple[str, ...] = field(default_factory=tuple)
    impacted_databases: tuple[str, ...] = field(default_factory=tuple)
    impacted_queues: tuple[str, ...] = field(default_factory=tuple)
    relationships: tuple[RelationshipInsight, ...] = field(default_factory=tuple)
    subgraph: GraphPayload = field(default_factory=GraphPayload)


@dataclass(frozen=True)
class QueryResult:
    relationships: tuple[RelationshipInsight, ...] = field(default_factory=tuple)
    total_matched: int = 0


@dataclass(frozen=True)
class ArchitectureFinding:
    finding_type: Literal[
        "dependency_cycle", "shared_database", "tightly_coupled_services", "ownership_gap"
    ]
    description: str
    involved_repositories: tuple[str, ...]
    confidence_state: str | None
    evidence_relationship_keys: tuple[str, ...] = field(default_factory=tuple)


ChangeType = Literal[
    "remove_endpoint", "remove_topic", "rename_api", "upgrade_dependency", "migrate_database"
]


@dataclass(frozen=True)
class ChangeImpact:
    entity: EntityReference
    change_type: ChangeType
    blast_radius: BlastRadius
    risk_summary: str


@dataclass(frozen=True)
class ServiceRequest:
    """One already-decided call into this layer. `OrganizationKnowledgeService`
    only ever executes a list of these — it never decides which service to
    call itself (that classification is agent-owned prompt logic)."""

    service: Literal[
        "repository_profile",
        "impact_analysis",
        "dependency_query",
        "architecture_insight",
    ]
    arguments: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ComposedAnswer:
    requests: tuple[ServiceRequest, ...]
    results: tuple[object, ...]
    errors: tuple[str, ...] = field(default_factory=tuple)
    computed_at: datetime = field(default_factory=lambda: datetime.now(UTC))
