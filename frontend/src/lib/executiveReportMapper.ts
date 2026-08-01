/**
 * Data mapper — transforms raw ExecutiveReportData from the API into
 * presentation-ready props for each dashboard section. Keeps components
 * free of data-munging logic.
 */

import type {
  ExecutiveReportData,
  StageMetrics,
  ReviewCategory,
  RepositoryImpactData,
  RecommendationData,
} from "../types/executiveReport";
import type { StatusTone } from "../components/StatusBadge";

// ---------------------------------------------------------------------------
// Formatting utilities
// ---------------------------------------------------------------------------

export function formatDuration(ms: number | null): string {
  if (ms === null || ms === 0) return "\u2014";
  const seconds = ms / 1000;
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const minutes = seconds / 60;
  if (minutes < 60) return `${minutes.toFixed(1)}m`;
  const hours = minutes / 60;
  return `${hours.toFixed(1)}h`;
}

export function formatTokens(tokens: number): string {
  if (tokens >= 1_000_000) return `${(tokens / 1_000_000).toFixed(1)}M`;
  if (tokens >= 1_000) return `${(tokens / 1_000).toFixed(1)}K`;
  return String(tokens);
}

export function formatCost(cost: number): string {
  if (cost === 0) return "$0.00";
  if (cost < 0.01) return `$${cost.toFixed(4)}`;
  return `$${cost.toFixed(2)}`;
}

export function formatConfidence(score: number | null): string {
  if (score === null) return "\u2014";
  return `${(score * 100).toFixed(0)}%`;
}

// ---------------------------------------------------------------------------
// Status → tone mapping
// ---------------------------------------------------------------------------

export function statusToTone(status: string): StatusTone {
  const lower = status.toLowerCase();
  if (["completed", "approved", "ready", "pass"].includes(lower)) return "success";
  if (["running", "in_progress"].includes(lower)) return "info";
  if (["conditional", "partial", "warning"].includes(lower)) return "warning";
  if (["failed", "not_ready", "fail", "rejected", "danger"].includes(lower)) return "danger";
  return "neutral";
}

export function stageStatusToTone(status: string): StatusTone {
  switch (status) {
    case "completed":
      return "success";
    case "running":
      return "info";
    case "failed":
      return "danger";
    default:
      return "neutral";
  }
}

// ---------------------------------------------------------------------------
// Section-specific mapped types
// ---------------------------------------------------------------------------

export interface SummaryProps {
  title: string;
  description: string;
  status: string;
  statusTone: StatusTone;
  duration: string;
  cost: string;
  tokens: string;
  confidence: string;
  workflowType: string;
  approvedBy: string | null;
}

export interface TimelineStage {
  name: string;
  label: string;
  status: "completed" | "running" | "failed" | "skipped" | "pending";
}

export interface MetricsTableRow {
  stage: string;
  model: string;
  tokens: string;
  latency: string;
  cost: string;
}

export interface MetricsSummary {
  primaryModel: string;
  primaryProvider: string;
  totalCalls: number;
  rows: MetricsTableRow[];
}

export interface ChartBar {
  label: string;
  value: number;
  formatted: string;
}

export interface ChartData {
  duration: ChartBar[];
  tokens: ChartBar[];
  cost: ChartBar[];
}

export interface ImpactProps {
  repositoryCount: number;
  filesChanged: number;
  componentCount: number;
  repositories: string[];
  components: string[];
  dependencies: string[];
}

export interface ReviewRow {
  category: string;
  status: string;
  statusTone: StatusTone;
  summary: string;
}

export interface RecommendationsProps {
  mergeReadiness: string;
  mergeReadinessTone: StatusTone;
  risks: string[];
  nextActions: string[];
  blockingItems: string[];
}

// ---------------------------------------------------------------------------
// Mappers
// ---------------------------------------------------------------------------

const STAGE_ORDER = [
  "context_discovery",
  "planning",
  "development",
  "testing",
  "documentation_planning",
  "engineering_review",
];

function stageLabel(stage: string): string {
  return stage
    .split("_")
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

export function mapSummary(data: ExecutiveReportData): SummaryProps {
  return {
    title: data.workflow_title,
    description: data.original_prompt.slice(0, 200),
    status: data.status.replace(/_/g, " "),
    statusTone: statusToTone(data.status),
    duration: formatDuration(data.duration_ms),
    cost: formatCost(data.total_cost_usd),
    tokens: formatTokens(data.total_tokens),
    confidence: formatConfidence(data.overall_confidence),
    workflowType: data.workflow_type.replace(/_/g, " "),
    approvedBy: data.approved_by,
  };
}

export function mapTimeline(data: ExecutiveReportData): TimelineStage[] {
  const stageMap = new Map(data.stages.map((s) => [s.stage, s]));
  return STAGE_ORDER.map((name) => {
    const stage = stageMap.get(name);
    let status: TimelineStage["status"] = "pending";
    if (stage) {
      if (stage.status === "completed") status = "completed";
      else if (stage.status === "running") status = "running";
      else if (stage.status === "failed") status = "failed";
      else status = "skipped";
    }
    return { name, label: stageLabel(name), status };
  });
}

export function mapMetrics(data: ExecutiveReportData): MetricsSummary {
  return {
    primaryModel: data.primary_model ?? "\u2014",
    primaryProvider: data.primary_provider ?? "\u2014",
    totalCalls: data.total_llm_calls,
    rows: data.stages.map((s) => ({
      stage: stageLabel(s.stage),
      model: s.model ?? "\u2014",
      tokens: formatTokens(s.total_tokens),
      latency: formatDuration(s.latency_ms),
      cost: formatCost(s.estimated_cost_usd),
    })),
  };
}

export function mapCharts(data: ExecutiveReportData): ChartData {
  return {
    duration: data.stages.map((s) => ({
      label: stageLabel(s.stage),
      value: s.duration_ms ?? 0,
      formatted: formatDuration(s.duration_ms),
    })),
    tokens: data.stages.map((s) => ({
      label: stageLabel(s.stage),
      value: s.total_tokens,
      formatted: formatTokens(s.total_tokens),
    })),
    cost: data.stages.map((s) => ({
      label: stageLabel(s.stage),
      value: s.estimated_cost_usd,
      formatted: formatCost(s.estimated_cost_usd),
    })),
  };
}

export function mapRepositoryImpact(data: ExecutiveReportData): ImpactProps {
  const impact = data.repository_impact;
  return {
    repositoryCount: impact.repositories_affected.length,
    filesChanged: impact.files_changed,
    componentCount: impact.components_affected.length,
    repositories: impact.repositories_affected,
    components: impact.components_affected,
    dependencies: impact.dependency_impact,
  };
}

export function mapReviewResults(data: ExecutiveReportData): ReviewRow[] {
  return data.review_results.map((r) => ({
    category: r.category,
    status: r.status.replace(/_/g, " "),
    statusTone: statusToTone(r.status),
    summary: r.summary || (r.issues.length > 0 ? r.issues.slice(0, 2).join("; ") : "\u2014"),
  }));
}

export function mapRecommendations(data: ExecutiveReportData): RecommendationsProps {
  const rec = data.recommendations;
  return {
    mergeReadiness: rec.merge_readiness.replace(/_/g, " "),
    mergeReadinessTone: statusToTone(rec.merge_readiness),
    risks: rec.risks,
    nextActions: rec.next_actions,
    blockingItems: rec.blocking_items,
  };
}
