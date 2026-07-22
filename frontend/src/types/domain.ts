/**
 * Shared domain types for the UI. These model what the backend will
 * eventually return; every value currently rendered against them comes
 * from `lib/mock`, not a live API call.
 */

export type RiskLevel = "critical" | "high" | "medium" | "low";

export type PullRequestStatus = "analyzing" | "open" | "blocked" | "merged";

export type RepositoryHealth = "healthy" | "attention" | "critical";

export type ReportStatus = "ready" | "generating" | "failed";

export interface PullRequest {
  id: string;
  title: string;
  repository: string;
  author: string;
  status: PullRequestStatus;
  risk: RiskLevel;
  affectedServices: number;
  updatedAt: string;
}

export interface Repository {
  id: string;
  name: string;
  provider: string;
  services: number;
  openPullRequests: number;
  health: RepositoryHealth;
  lastAnalyzed: string;
}

export interface ServiceNode {
  id: string;
  name: string;
  repository: string;
  dependents: number;
  dependencies: number;
  risk: RiskLevel;
}

export interface Report {
  id: string;
  name: string;
  repository: string;
  risk: RiskLevel;
  status: ReportStatus;
  generatedAt: string;
}
