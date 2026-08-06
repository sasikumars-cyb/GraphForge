/**
 * API functions for the Reports/metrics dashboard endpoint.
 * Follows the existing apiFetch convention from client.ts.
 */

import { apiFetch } from "./client";
import type {
  MetricsReportResponse,
  MetricsScope,
  WorkflowLLMUsageResponse,
} from "../../types/metrics";

export interface GetReportParams {
  scope?: MetricsScope;
  window_days?: number;
}

export function getMetricsReport(
  token: string,
  params: GetReportParams = {},
  signal?: AbortSignal,
): Promise<MetricsReportResponse> {
  const searchParams = new URLSearchParams();
  if (params.scope) searchParams.set("scope", params.scope);
  if (params.window_days) searchParams.set("window_days", String(params.window_days));

  const qs = searchParams.toString();
  return apiFetch<MetricsReportResponse>(`/metrics/report${qs ? `?${qs}` : ""}`, {
    token,
    signal,
  });
}

/** Per-stage LLM usage (model, tokens, cost, latency, call count) for one
 * workflow — what a "Recent Workflows" row on the Metrics page links to. */
export function getWorkflowLLMUsage(
  token: string,
  workflowId: string,
  signal?: AbortSignal,
): Promise<WorkflowLLMUsageResponse> {
  return apiFetch<WorkflowLLMUsageResponse>(`/metrics/workflows/${workflowId}/llm-usage`, {
    token,
    signal,
  });
}
