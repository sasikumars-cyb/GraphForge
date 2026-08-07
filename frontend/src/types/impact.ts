import type { Graph } from "./graph";

/** Mirrors backend/app/schemas/impact.py. */
export interface RelationshipInsight {
  relationship_type: string;
  source_entity: string;
  target_entity: string;
  /** One of verified/highly_likely/likely/candidate/rejected/conflicting. */
  confidence_state: string;
}

export interface BlastRadius {
  seed_node_id: string;
  max_hops: number;
  /** Every impacted node, each carrying `hop_distance` in its own
   * `properties` — what the radial graph groups nodes into rings by. */
  graph: Graph;
  impacted_repositories: string[];
  impacted_apis: string[];
  impacted_databases: string[];
  impacted_queues: string[];
  relationships: RelationshipInsight[];
}
