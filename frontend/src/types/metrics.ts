/** Types for the in-app Reports/metrics dashboard — mirrors
 * `backend/app/schemas/metrics.py`'s `MetricsReportResponse`.
 */

export interface MetricsOverview {
  total_workflows: number;
  completed_workflows: number;
  completed_runs: number;
  total_llm_calls: number;
  total_tokens: number;
  total_cost_usd: number;
  avg_latency_ms: number;
  indexed_repositories: number;
  total_graph_nodes: number;
  total_graph_edges: number;
}

export interface CostByDayPoint {
  day: string;
  cost_usd: number;
  tokens: number;
}

export interface ProviderCost {
  provider: string;
  calls: number;
  cost_usd: number;
  tokens: number;
}

export interface StageCost {
  stage: string;
  calls: number;
  cost_usd: number;
  tokens: number;
}

export interface ModelUsage {
  model: string;
  provider: string;
  calls: number;
  cost_usd: number;
}

export interface RunStageOutcome {
  stage: string;
  total: number;
  succeeded: number;
  failed: number;
}

export interface RepositoryComponentCount {
  repository_id: string;
  name: string;
  components: number;
}

export interface WorkflowSummary {
  id: string;
  title: string;
  status: string;
  current_stage: string;
  workflow_type: string;
  created_at: string;
  updated_at: string;
  cost_usd: number;
  tokens: number;
}

export type MetricsScope = "user" | "global";

export interface MetricsReportResponse {
  scope: MetricsScope;
  generated_at: string;
  window_days: number;
  overview: MetricsOverview;
  cost_by_day: CostByDayPoint[];
  cost_by_provider: ProviderCost[];
  cost_by_stage: StageCost[];
  model_usage: ModelUsage[];
  run_success_by_stage: RunStageOutcome[];
  repository_components: RepositoryComponentCount[];
  recent_workflows: WorkflowSummary[];
}
