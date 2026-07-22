/**
 * Types for the architecture graph API — mirrors backend/app/schemas/indexing.py's
 * `GraphResponse` (itself a Pydantic mirror of `app.graph.models`).
 */

export interface GraphNode {
  id: string;
  labels: string[];
  properties: Record<string, unknown>;
}

export interface GraphEdge {
  source_id: string;
  target_id: string;
  type: string;
  properties: Record<string, unknown>;
}

export interface Graph {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

/** Mirrors backend/app/schemas/indexing.py's `CrossRepositoryLinkResponse`. */
export interface CrossRepositoryLink {
  repository_id: string;
  repository_name: string;
  component_id: string;
  component_name: string;
  relationship: string;
  topic_name: string;
}

export interface IndexingJob {
  id: string;
  repository_id: string;
  status: "pending" | "running" | "completed" | "failed";
  error_message: string | null;
  result_summary: Record<string, number> | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
}
