/**
 * Types for the Executive Report dashboard — mirrors the backend
 * ExecutiveReportData schema from executive_report.py.
 */

export interface StageMetrics {
  stage: string;
  status: string;
  duration_ms: number | null;
  confidence_score: number | null;
  model: string | null;
  provider: string | null;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  estimated_cost_usd: number;
  latency_ms: number | null;
  retry_count: number;
}

export interface ReviewCategory {
  category: string;
  status: string;
  summary: string;
  issues: string[];
}

export interface RepositoryImpactData {
  repositories_affected: string[];
  files_changed: number;
  components_affected: string[];
  dependency_impact: string[];
}

export interface RecommendationData {
  merge_readiness: string;
  risks: string[];
  next_actions: string[];
  blocking_items: string[];
}

export interface ExecutiveReportData {
  workflow_id: string;
  workflow_title: string;
  original_prompt: string;
  workflow_type: string;
  status: string;
  current_stage: string;
  created_at: string;
  completed_at: string | null;
  duration_ms: number | null;
  approved_by: string | null;
  total_tokens: number;
  total_cost_usd: number;
  total_llm_calls: number;
  primary_model: string | null;
  primary_provider: string | null;
  overall_confidence: number | null;
  stages: StageMetrics[];
  repository_impact: RepositoryImpactData;
  review_results: ReviewCategory[];
  recommendations: RecommendationData;
}
