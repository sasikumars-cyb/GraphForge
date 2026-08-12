import type { ProvenanceKind } from "../components/intelligence/ProvenanceTag";

export type AskIntent = "impact" | "dependency" | "general";
export type AskStatus = "answered" | "route_to_investigation";

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
}

export interface AskResponse {
  status: AskStatus;
  question: string;
  intent: AskIntent;
  resolved_repository_id: string | null;
  resolved_repository_name: string | null;
  answer: string;
  why: string;
  evidence: AskEvidenceItem[];
  impact: AskImpact | null;
  actions: AskAction[];
}
