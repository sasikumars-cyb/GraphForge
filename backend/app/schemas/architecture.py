"""Response schemas for GET /architecture/summary (ADR 0023) — the
org-scale replacement for ArchitecturePage.tsx's per-repository
GET /repositories/{id}/index fan-out.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel


class RepositorySummary(BaseModel):
    repository_id: uuid.UUID
    name: str
    full_name: str
    # ADR 0023 — manual grouping only; None means ungrouped.
    domain: str | None = None
    # None = never indexed. Otherwise the latest IndexingJob's status
    # ("pending" | "running" | "completed" | "failed").
    indexing_status: str | None = None
    last_indexed_at: datetime | None = None
    node_count: int = 0
    # Excludes the base "GraphNode" label — every node carries it, so
    # it's never a meaningful per-type breakdown entry.
    node_counts_by_label: dict[str, int] = {}
    # True when `last_indexed_at` is older than the summary's own
    # staleness threshold, or when it's never been indexed at all — see
    # `architecture.py`'s `_STALE_THRESHOLD_DAYS`.
    is_stale: bool = False


class DomainSummary(BaseModel):
    # None groups every repository with no domain assigned — surfaced as
    # its own entry ("Ungrouped" is a frontend label choice), not omitted.
    domain: str | None
    repository_count: int
    node_count: int


class ArchitectureSummaryResponse(BaseModel):
    total_repositories: int
    total_nodes: int
    total_cross_repository_edges: int
    repositories: list[RepositorySummary]
    domains: list[DomainSummary]
    unindexed_count: int
    stale_count: int
