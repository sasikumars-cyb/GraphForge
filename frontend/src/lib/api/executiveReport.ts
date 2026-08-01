/**
 * API client for the Executive Report endpoints.
 */

import { apiFetch, API_BASE_URL } from "./client";
import type { ExecutiveReportData } from "../../types/executiveReport";

export function getExecutiveReportData(
  token: string,
  reportId: string,
  signal?: AbortSignal,
): Promise<ExecutiveReportData> {
  return apiFetch<ExecutiveReportData>(
    `/reports/${encodeURIComponent(reportId)}/executive-data`,
    { token, signal },
  );
}

/**
 * Returns the URL for the self-contained executive HTML report.
 * Used for iframe src or direct download.
 */
export function getExecutiveReportHtmlUrl(reportId: string): string {
  return `${API_BASE_URL}/reports/${encodeURIComponent(reportId)}/executive-html`;
}

/**
 * Fetches the executive report as a self-contained HTML string for download.
 */
export async function downloadExecutiveReportHtml(
  token: string,
  reportId: string,
): Promise<string> {
  const url = `${API_BASE_URL}/reports/${encodeURIComponent(reportId)}/executive-html`;
  const response = await fetch(url, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!response.ok) {
    throw new Error(`Failed to download report: ${response.status}`);
  }
  return response.text();
}
