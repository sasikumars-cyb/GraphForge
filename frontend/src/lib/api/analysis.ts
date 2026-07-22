import { apiFetch } from "./client";
import type { AIAnalysis, AIAnalysisResult, PullRequestAnalysis } from "../../types/analysis";

export function runDeterministicAnalysis(
  token: string,
  pullRequestId: string,
): Promise<PullRequestAnalysis> {
  return apiFetch<PullRequestAnalysis>(`/pull-requests/${pullRequestId}/analyze`, {
    method: "POST",
    token,
  });
}

export function getDeterministicAnalysis(
  token: string,
  pullRequestId: string,
): Promise<PullRequestAnalysis> {
  return apiFetch<PullRequestAnalysis>(`/pull-requests/${pullRequestId}/analysis`, { token });
}

export function runAiAnalysis(
  token: string,
  pullRequestId: string,
  model?: string,
): Promise<AIAnalysisResult> {
  return apiFetch<AIAnalysisResult>(`/pull-requests/${pullRequestId}/ai-analysis`, {
    method: "POST",
    token,
    body: { model: model ?? null },
  });
}

export function getAiAnalysis(token: string, pullRequestId: string): Promise<AIAnalysis> {
  return apiFetch<AIAnalysis>(`/pull-requests/${pullRequestId}/ai-analysis`, { token });
}
