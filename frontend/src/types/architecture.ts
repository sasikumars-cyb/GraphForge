/**
 * Types for the Architecture API v2 endpoints (ADR 0023) — mirrors
 * backend/app/schemas/architecture.py and the additions to
 * backend/app/schemas/indexing.py.
 */

export interface ArchitectureRepositorySummary {
  repository_id: string;
  name: string;
  full_name: string;
  domain: string | null;
  indexing_status: "pending" | "running" | "completed" | "failed" | null;
  last_indexed_at: string | null;
  node_count: number;
  node_counts_by_label: Record<string, number>;
  is_stale: boolean;
}

export interface DomainSummary {
  /** `null` groups every repository with no domain assigned. */
  domain: string | null;
  repository_count: number;
  node_count: number;
}

export interface ArchitectureSummary {
  total_repositories: number;
  total_nodes: number;
  total_cross_repository_edges: number;
  repositories: ArchitectureRepositorySummary[];
  domains: DomainSummary[];
  unindexed_count: number;
  stale_count: number;
}

export interface NodeTypeCounts {
  counts: Record<string, number>;
}
