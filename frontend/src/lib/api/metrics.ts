/**
 * API functions for the Reports/metrics dashboard endpoint.
 * Follows the existing apiFetch convention from client.ts.
 */

import { apiFetch } from "./client";
import type { MetricsReportResponse, MetricsScope } from "../../types/metrics";

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
