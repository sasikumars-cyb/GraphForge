/**
 * Derived operational signals for the Metrics page — "what should I pay
 * attention to", computed deterministically from `MetricsReportResponse`
 * data the backend already returns. Nothing here is an LLM claim; every
 * signal is arithmetic over real counts, which is why the page tags each
 * one `derived` (see `ProvenanceTag`) rather than `ai_insight`. A signal
 * that isn't well-supported by the data (too few data points, nothing
 * actually stands out) simply isn't returned — no signal is invented to
 * fill space.
 */
import type { CostByDayPoint, RunStageOutcome, StageCost } from "../types/metrics";

export interface StageFailureSignal {
  kind: "stage_failure";
  stage: string;
  failureRate: number; // 0-1
  failed: number;
  total: number;
}

export interface StageCostSignal {
  kind: "stage_cost";
  stage: string;
  costUsd: number;
  shareOfTotal: number; // 0-1
}

export interface CostTrendSignal {
  kind: "cost_trend";
  direction: "up" | "down";
  changeFraction: number; // absolute fraction, e.g. 0.42 for +42%
  recentCostUsd: number;
  priorCostUsd: number;
}

export type MetricsSignal = StageFailureSignal | StageCostSignal | CostTrendSignal;

const MIN_RUNS_FOR_FAILURE_SIGNAL = 3;
const NOTABLE_FAILURE_RATE = 0.15;
const NOTABLE_COST_SHARE = 0.35;
const MIN_DAYS_FOR_TREND = 4;
const NOTABLE_TREND_CHANGE = 0.2;

/** The stage with the worst failure rate, only surfaced if it has enough
 * runs to mean something (three near-misses is noise, thirty isn't) and
 * the rate itself clears a bar worth a human's attention. */
function worstStageFailure(rows: RunStageOutcome[]): StageFailureSignal | null {
  let worst: StageFailureSignal | null = null;
  for (const row of rows) {
    if (row.total < MIN_RUNS_FOR_FAILURE_SIGNAL) continue;
    const failureRate = row.failed / row.total;
    if (failureRate < NOTABLE_FAILURE_RATE) continue;
    if (!worst || failureRate > worst.failureRate) {
      worst = { kind: "stage_failure", stage: row.stage, failureRate, failed: row.failed, total: row.total };
    }
  }
  return worst;
}

/** The single stage consuming a disproportionate share of total spend —
 * only surfaced once it's genuinely the largest slice of a real pie, not
 * whenever there happen to be stage costs at all. */
function dominantStageCost(rows: StageCost[]): StageCostSignal | null {
  const total = rows.reduce((sum, r) => sum + r.cost_usd, 0);
  if (total <= 0) return null;
  const top = [...rows].sort((a, b) => b.cost_usd - a.cost_usd)[0];
  if (!top) return null;
  const shareOfTotal = top.cost_usd / total;
  if (shareOfTotal < NOTABLE_COST_SHARE) return null;
  return { kind: "stage_cost", stage: top.stage, costUsd: top.cost_usd, shareOfTotal };
}

/** Second half of the window vs the first half, by total cost — a coarse
 * but honest trend read that needs no more than the same `cost_by_day`
 * series the chart above it already renders. Requires enough days that
 * "half of the window" isn't one or two data points wobbling. */
function costTrend(points: CostByDayPoint[]): CostTrendSignal | null {
  if (points.length < MIN_DAYS_FOR_TREND) return null;
  const sorted = [...points].sort((a, b) => a.day.localeCompare(b.day));
  const mid = Math.floor(sorted.length / 2);
  const prior = sorted.slice(0, mid);
  const recent = sorted.slice(mid);
  const priorCostUsd = prior.reduce((sum, p) => sum + p.cost_usd, 0);
  const recentCostUsd = recent.reduce((sum, p) => sum + p.cost_usd, 0);
  if (priorCostUsd <= 0) return null; // no baseline to compare against
  const changeFraction = (recentCostUsd - priorCostUsd) / priorCostUsd;
  if (Math.abs(changeFraction) < NOTABLE_TREND_CHANGE) return null;
  return {
    kind: "cost_trend",
    direction: changeFraction >= 0 ? "up" : "down",
    changeFraction: Math.abs(changeFraction),
    recentCostUsd,
    priorCostUsd,
  };
}

/** Every notable signal the current report supports, most actionable
 * first (a failing stage before a cost trend). Capped at three by
 * construction (one per kind) rather than an arbitrary slice — this isn't
 * "top N of many candidates", it's "these specific three questions,
 * answered when the data actually supports an answer". */
export function computeMetricsSignals(report: {
  run_success_by_stage: RunStageOutcome[];
  cost_by_stage: StageCost[];
  cost_by_day: CostByDayPoint[];
}): MetricsSignal[] {
  return [
    worstStageFailure(report.run_success_by_stage),
    dominantStageCost(report.cost_by_stage),
    costTrend(report.cost_by_day),
  ].filter((s): s is MetricsSignal => s !== null);
}
