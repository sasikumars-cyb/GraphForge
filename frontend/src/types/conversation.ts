import type {
  AskAction,
  AskEvidenceItem,
  AskImpact,
  AskRepositoryCandidate,
} from "./ask";
import type { ProvenanceKind } from "../components/intelligence/ProvenanceTag";

export type ConversationMode = "general" | "migration" | "refinement";

export interface ConversationEntityRef {
  ref: string;
  name: string;
  impact_level: "low" | "medium" | "high" | null;
}

export interface MigrationRisk {
  label: string;
  reason: string;
  provenance: AskEvidenceItem["provenance"];
}

export interface MigrationScope {
  source_technology: string;
  target_technology: string;
  direct: string[];
  indirect: string[];
  risks: MigrationRisk[];
  primary_repository_id: string | null;
}

export type WorkItemType = "epic" | "story" | "task" | "spike";
export type WorkItemStatus = "existing" | "proposed";
export type EdgeRelationship = "blocks" | "depends_on" | "enables" | "related" | "parent_child";
export type QuestionCategory = "known" | "derived" | "assumption" | "unknown";
export type ReadinessLevel = "ready" | "mostly_ready" | "needs_clarification" | "not_ready";

export interface WorkItem {
  id: string;
  type: WorkItemType;
  status: WorkItemStatus;
  title: string;
  objective: string;
  context: string;
  scope: string;
  acceptance_criteria: string[];
  related_systems: string[];
  risks: string[];
  evidence_note: string;
  provenance: ProvenanceKind;
}

export interface WorkItemEdge {
  source_id: string;
  target_id: string;
  relationship: EdgeRelationship;
  provenance: ProvenanceKind;
  source_system: "jira" | "refinement_analysis";
}

export interface OpenQuestion {
  question: string;
  category: QuestionCategory;
  note: string;
}

export interface Spike {
  work_item_id: string;
  why: string;
  questions: string[];
  exit_criteria: string;
}

export interface RefinementReadiness {
  level: ReadinessLevel;
  score: number;
  ready_signals: string[];
  needs_clarification: string[];
  investigation_required: string[];
}

export interface RefinementPlan {
  requirement_summary: string;
  objective: string;
  desired_outcome: string;
  scope: string[];
  out_of_scope: string[];
  functional_requirements: string[];
  non_functional_requirements: string[];
  constraints: string[];
  assumptions: string[];
  missing_work_categories: string[];
  work_items: WorkItem[];
  edges: WorkItemEdge[];
  spikes: Spike[];
  open_questions: OpenQuestion[];
  engineering_context_grounded: boolean;
  readiness: RefinementReadiness | null;
  critical_paths: string[][];
  parallelizable_ids: string[];
  unresolved_source_note: string;
  source_jira_key: string | null;
  source_jira_url: string | null;
}

export interface ConversationTurnPayload {
  intent: string;
  resolved_repository_id: string | null;
  resolved_repository_name: string | null;
  why: string;
  evidence: AskEvidenceItem[];
  impact: AskImpact | null;
  actions: AskAction[];
  entities: ConversationEntityRef[];
  /** This turn could not confidently identify which system the question
   * is about, so it asks instead of answering. Carries no evidence and no
   * impact by construction — see `_clarification_turn` on the backend. */
  needs_clarification: boolean;
  candidates: AskRepositoryCandidate[];
  migration: MigrationScope | null;
  refinement: RefinementPlan | null;
  degraded: boolean;
}

export interface ConversationMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  payload: ConversationTurnPayload | null;
  created_at: string;
}

export interface Conversation {
  id: string;
  title: string;
  mode: ConversationMode;
  created_at: string;
  updated_at: string;
  messages: ConversationMessage[];
}

export interface ConversationSummary {
  id: string;
  title: string;
  mode: ConversationMode;
  created_at: string;
  updated_at: string;
}
