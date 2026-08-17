import type { ProvenanceKind } from "../components/intelligence/ProvenanceTag";

export type AskIntent = "impact" | "dependency" | "general";
/** Matches `app.schemas.ask.AskResponse.status` exactly. "needs_clarification"
 * — the question was understood but GraphForge couldn't confidently identify
 * which system it means; `candidates` on `AskResponse` carries what it did
 * find. No `answer`/`evidence`/`impact` accompany that state. */
export type AskStatus = "answered" | "needs_clarification" | "route_to_investigation";

export interface AskAction {
  label: string;
  kind:
    | "explore_impact"
    | "view_repository"
    | "view_dependency_graph"
    | "investigate"
    | "create_migration_plan"
    | "validate_migration"
    | "view_work_graph"
    | "create_planning_workflow"
    | "generate_testing_strategy"
    | "view_jira_issue";
  href: string;
}

export interface AskEvidenceItem {
  source: string;
  label: string;
  provenance: ProvenanceKind;
}

export interface AskImpact {
  severity: "low" | "medium" | "high";
  summary: string;
  affected_repositories: string[];
  affected_apis: string[];
  affected_databases: string[];
  affected_queues: string[];
  /** True when the blast radius exceeded the reporting limit and the
   * lists above are a bounded sample — never present a truncated result
   * as an exhaustive impact analysis. */
  truncated: boolean;
}

/** One repository GraphForge considered but could not confidently choose
 * between. Offered to the user so an ambiguous question becomes a
 * clarification instead of a wrong answer. */
export interface AskRepositoryCandidate {
  name: string;
  full_name: string;
  repository_id: string;
  score: number;
}

export interface AskResponse {
  status: AskStatus;
  question: string;
  intent: AskIntent;
  /** Why resolution ended where it did — "strong_match", "exact_name_match",
   * "candidates_too_close", "only_generic_terms_matched",
   * "below_minimum_confidence", "no_deterministic_path_for_question", ...
   * Empty string when the question resolved cleanly. */
  resolution_reason: string;
  /** Repositories GraphForge considered but couldn't confidently choose
   * between — only populated when `status === "needs_clarification"` (and,
   * less commonly, alongside "route_to_investigation" when a partial match
   * existed but fell short of resolving). */
  candidates: AskRepositoryCandidate[];
  resolved_repository_id: string | null;
  resolved_repository_name: string | null;
  answer: string;
  why: string;
  evidence: AskEvidenceItem[];
  impact: AskImpact | null;
  actions: AskAction[];
}
