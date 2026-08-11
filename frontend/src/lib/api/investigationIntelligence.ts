/**
 * API client for GET /investigation-intelligence/summary — mirrors
 * backend/app/api/v1/routers/investigation_intelligence.py's response
 * models field-for-field. Only `repeated_failure_groups` (plus
 * `window_days` for framing it) is consumed by Mission Control's Agent
 * Insights panel today; the rest of the response is typed for fidelity
 * but not yet read by any page.
 *
 * IMPORTANT — what this data actually is: `RepeatedFailureGroup` describes
 * a knowledge-*provider* (Jira, Confluence, the architecture graph, …)
 * repeatedly failing or coming back "unavailable" for a given capability
 * during recent investigations. It is a retrieval-reliability signal, not
 * a claim about a bug in the user's own code — see
 * MissionControlPage/AgentInsightsPanel for the copy this drives.
 */
import { apiFetch } from "./client";

export interface OutcomeCount {
  outcome: string;
  count: number;
}

export interface ProviderStat {
  provider: string;
  capability: string;
  total: number;
  success: number;
  success_rate: number;
  avg_latency_ms: number;
  outcome_counts: OutcomeCount[];
}

export interface DistributionBucket {
  bucket: string;
  count: number;
}

export interface CycleStat {
  terminal_outcome: string;
  count: number;
  avg_cycles: number;
}

export interface PriorityBoostUsage {
  total_events: number;
  boosted_events: number;
  boost_usage_rate: number;
  memory_influenced_events: number;
  memory_hit_rate: number;
}

/** A `(scope, capability, provider)` triple that failed/was-unavailable
 * repeatedly within the window — see this module's own docstring for what
 * that does and doesn't imply. */
export interface RepeatedFailureGroup {
  scope_type: string;
  scope_id: string;
  capability: string;
  provider: string;
  failure_count: number;
  most_recent_at: string;
}

export interface InvestigationIntelligenceSummaryResponse {
  window_days: number;
  total_provider_events: number;
  total_investigations: number;
  providers: ProviderStat[];
  confidence_improvement_distribution: DistributionBucket[];
  latency_distribution: DistributionBucket[];
  cycles_by_terminal_outcome: CycleStat[];
  priority_boost_usage: PriorityBoostUsage;
  repeated_failure_groups: RepeatedFailureGroup[];
}

export function getInvestigationIntelligenceSummary(
  token: string,
  signal?: AbortSignal,
): Promise<InvestigationIntelligenceSummaryResponse> {
  return apiFetch<InvestigationIntelligenceSummaryResponse>("/investigation-intelligence/summary", {
    token,
    signal,
  });
}
