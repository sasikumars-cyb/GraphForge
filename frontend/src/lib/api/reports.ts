/**
 * API functions for the Workflow Reports endpoints — the high-level HTML
 * reports generated when a Planning workflow's blueprint is approved.
 */

import { apiFetch } from "./client";

export interface ReportSummary {
  id: string;
  workflow_id: string;
  workflow_title: string;
  title: string;
  status: "pending" | "completed" | "failed";
  error_message: string | null;
  created_at: string;
  completed_at: string | null;
}

export interface ReportDetail extends ReportSummary {
  html_content: string | null;
}

export function listReports(token: string, signal?: AbortSignal): Promise<ReportSummary[]> {
  return apiFetch<ReportSummary[]>("/reports", { token, signal });
}

export function getReport(
  token: string,
  reportId: string,
  signal?: AbortSignal,
): Promise<ReportDetail> {
  return apiFetch<ReportDetail>(`/reports/${encodeURIComponent(reportId)}`, { token, signal });
}
