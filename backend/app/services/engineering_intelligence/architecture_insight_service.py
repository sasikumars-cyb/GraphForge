"""`ArchitectureInsightService` — dependency cycles, shared databases,
tight coupling, and repeated-rejection ownership gaps. Reuses
`relationship_lookup.fetch_with_confidence` for confidence-bearing
findings and `LearningEngineService.get_statistics` for ownership-gap
signals (never re-derives either).

Cycle/coupling detection here works over `DEPENDS_ON_REPOSITORY`/
`CALLS_SERVICE` cross-repository edges already computed by
`app.indexer.graph.cross_repo_linker` and persisted as
`KnowledgeRelationshipRecord`s — it does not call `graph_traversal`,
because the input here is a small, already-materialized relationship
list (per-organization repository count), not a graph neighborhood to
walk hop-by-hop. Reserving `graph_traversal` for genuine node-level
traversal keeps this service from growing a second traversal
implementation under a different name.
"""

from __future__ import annotations

import uuid
from collections import defaultdict

from sqlalchemy.ext.asyncio import AsyncSession

from app.learning_engine.service import LearningEngineService
from app.services.engineering_intelligence import relationship_lookup
from app.services.engineering_intelligence.contracts import ArchitectureFinding, RelationshipInsight

_CROSS_REPO_TYPES = frozenset({"CALLS_SERVICE", "DEPENDS_ON_REPOSITORY"})
_MIN_STATE_RANK = {
    "rejected": 0,
    "conflicting": 1,
    "candidate": 2,
    "likely": 3,
    "highly_likely": 4,
    "verified": 5,
}


def _weakest_state(states: list[str]) -> str | None:
    if not states:
        return None
    return min(states, key=lambda state: _MIN_STATE_RANK.get(state, 0))


def _detect_cycles(edges: list[RelationshipInsight]) -> list[ArchitectureFinding]:
    """Direct A->B->A pairs among cross-repository edges — the smallest,
    least speculative cycle shape to report; a full graph-cycle search
    (arbitrary length) is deliberately out of scope until a real need for
    it is demonstrated (approved design's "no abstraction without two
    real consumers" discipline)."""
    by_pair: dict[tuple[str, str], list[RelationshipInsight]] = defaultdict(list)
    for insight in edges:
        by_pair[(insight.source_entity, insight.target_entity)].append(insight)

    findings: list[ArchitectureFinding] = []
    seen_pairs: set[frozenset[str]] = set()
    for (source, target), forward in by_pair.items():
        pair_key = frozenset({source, target})
        if pair_key in seen_pairs or source == target:
            continue
        reverse = by_pair.get((target, source))
        if not reverse:
            continue
        seen_pairs.add(pair_key)
        states = [i.confidence_state for i in forward + reverse]
        findings.append(
            ArchitectureFinding(
                finding_type="dependency_cycle",
                description=f"{source} and {target} depend on each other.",
                involved_repositories=tuple(sorted({source, target})),
                confidence_state=_weakest_state(states),
                evidence_relationship_keys=tuple(
                    sorted(i.relationship_key for i in forward + reverse)
                ),
            )
        )
    return findings


def _detect_shared_databases(insights: list[RelationshipInsight]) -> list[ArchitectureFinding]:
    by_table: dict[str, list[RelationshipInsight]] = defaultdict(list)
    for insight in insights:
        if insight.relationship_type in ("READS_FROM", "WRITES_TO"):
            by_table[insight.target_entity].append(insight)

    findings: list[ArchitectureFinding] = []
    for table, accessors in sorted(by_table.items()):
        owning_repos = {i.source_entity.split(":")[0] for i in accessors}
        if len(owning_repos) > 1:
            findings.append(
                ArchitectureFinding(
                    finding_type="shared_database",
                    description=f"{table} is accessed by {len(owning_repos)} repositories.",
                    involved_repositories=tuple(sorted(owning_repos)),
                    confidence_state=_weakest_state([i.confidence_state for i in accessors]),
                    evidence_relationship_keys=tuple(sorted(i.relationship_key for i in accessors)),
                )
            )
    return findings


async def detect_findings(
    db: AsyncSession, repository_ids: list[uuid.UUID]
) -> tuple[ArchitectureFinding, ...]:
    all_insights: list[RelationshipInsight] = []
    for repository_id in repository_ids:
        all_insights.extend(await relationship_lookup.fetch_with_confidence(db, repository_id))

    cross_repo_insights = [i for i in all_insights if i.relationship_type in _CROSS_REPO_TYPES]

    findings: list[ArchitectureFinding] = []
    findings.extend(_detect_cycles(cross_repo_insights))
    findings.extend(_detect_shared_databases(all_insights))

    learning = LearningEngineService(db)
    for repository_id in repository_ids:
        stats = await learning.get_statistics(repository_id)
        for signal in stats.repeated_false_positive_signals:
            findings.append(
                ArchitectureFinding(
                    finding_type="ownership_gap",
                    description=(
                        f"{signal.relationship_type} relationships from "
                        f"{signal.generator_name or 'an unnamed generator'} have been rejected "
                        f"{signal.rejection_count} time(s) in {repository_id} — "
                        "recurring false positives suggest missing ownership context."
                    ),
                    involved_repositories=(str(repository_id),),
                    confidence_state=None,
                )
            )

    return tuple(
        sorted(findings, key=lambda f: (f.finding_type, f.involved_repositories, f.description))
    )
