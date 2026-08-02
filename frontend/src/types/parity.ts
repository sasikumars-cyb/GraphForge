/** Mirrors the backend's `app.schemas.parity.ParityReportResponse` — the
 * Graph Parity Engine's report, unchanged by this dashboard, only
 * rendered. */

export type OverallResult = "pass" | "fail";

export interface PropertyDifference {
  key: string;
  legacy_value: string | null;
  materialized_value: string | null;
}

export interface NodeMismatch {
  node_id: string;
  label_differences: string[];
  property_differences: PropertyDifference[];
}

export interface EdgeSignature {
  source_id: string;
  target_id: string;
  type: string;
  properties_json: string;
}

export interface EdgePropertyMismatch {
  source_id: string;
  target_id: string;
  type: string;
  property_differences: PropertyDifference[];
}

export interface DuplicateEntity {
  key: string;
  legacy_count: number;
  materialized_count: number;
}

export interface IgnoredDifference {
  entity_kind: string;
  entity_key: string;
  property_name: string;
  reason: string;
}

export interface NodeStatistics {
  legacy_count: number;
  materialized_count: number;
  matched_count: number;
}

export interface EdgeStatistics {
  legacy_count: number;
  materialized_count: number;
  matched_count: number;
}

export interface ParityReport {
  overall_result: OverallResult;
  node_statistics: NodeStatistics;
  edge_statistics: EdgeStatistics;

  missing_nodes: string[];
  unexpected_nodes: string[];
  node_mismatches: NodeMismatch[];
  duplicate_nodes: DuplicateEntity[];

  missing_edges: EdgeSignature[];
  unexpected_edges: EdgeSignature[];
  edge_property_mismatches: EdgePropertyMismatch[];
  duplicate_edges: DuplicateEntity[];

  ignored_differences: IgnoredDifference[];

  similarity_percentage: number;
  summary: string;
}
