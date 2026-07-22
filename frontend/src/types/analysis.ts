/**
 * Types for the deterministic and AI pull-request analysis APIs — mirrors
 * backend/app/schemas/analysis.py and backend/app/schemas/ai_analysis.py.
 */

// --- Deterministic (Phase 7) ---

export interface ImpactedNode {
  id: string;
  name: string;
  node_type: string;
  repository_id: string;
}

export interface DependencyPathStep {
  node_id: string;
  node_name: string;
  node_type: string;
  relationship: string | null;
}

export interface DependencyPath {
  steps: DependencyPathStep[];
}

/** No "CRITICAL" tier exists on the backend - only these three values are ever produced. */
export type RiskLevel = "HIGH" | "MEDIUM" | "LOW";

export interface PullRequestAnalysis {
  id: string;
  pull_request_id: string;
  risk: RiskLevel;
  directly_impacted_services: ImpactedNode[];
  indirectly_impacted_services: ImpactedNode[];
  impacted_apis: ImpactedNode[];
  impacted_topics: ImpactedNode[];
  impacted_libraries: ImpactedNode[];
  dependency_paths: DependencyPath[];
  analyzed_at: string;
}

// --- AI (Phase 8) ---

export interface ConfidenceScore {
  score: number;
  reasoning: string;
}

export interface BreakingChange {
  component: string;
  description: string;
  severity: string;
  confidence: ConfidenceScore;
}

export interface MigrationAdvice {
  component: string;
  advice: string;
  priority: string;
}

export interface SuggestedReviewer {
  reviewer: string;
  reason: string;
  confidence: ConfidenceScore;
}

export interface RegressionTest {
  component: string;
  test_description: string;
  priority: string;
  confidence: ConfidenceScore;
}

export interface DeploymentStep {
  order: number;
  repository: string;
  action: string;
  reason: string;
}

export interface RepositoryToNotify {
  repository: string;
  reason: string;
  urgency: "blocking" | "advisory";
}

export interface ReleaseCoordinationPlan {
  deployment_order: DeploymentStep[];
  repositories_to_notify: RepositoryToNotify[];
  rollout_strategy: string;
  backward_compatibility_advice: string;
  communication_summary: string;
  rollout_risks: string[];
}

/** GET /pull-requests/{id}/ai-analysis - flat, hydrated straight from ORM columns. */
export interface AIAnalysis {
  id: string;
  pull_request_id: string;
  executive_summary: string;
  breaking_changes: BreakingChange[];
  migration_advice: MigrationAdvice[];
  suggested_reviewers: SuggestedReviewer[];
  regression_tests: RegressionTest[];
  confidence_score: number;
  confidence_reasoning: string;
  prompt_version: string;
  analyzed_at: string;
}

/**
 * POST /pull-requests/{id}/ai-analysis - nested, includes the Release
 * Coordination Plan, which is ephemeral (never persisted, only returned live).
 */
export interface AIAnalysisResult {
  executive_summary: string;
  breaking_changes: BreakingChange[];
  migration_advice: MigrationAdvice[];
  suggested_reviewers: SuggestedReviewer[];
  regression_tests: RegressionTest[];
  release_coordination_plan: ReleaseCoordinationPlan;
  confidence: ConfidenceScore;
  prompt_version: string;
}
