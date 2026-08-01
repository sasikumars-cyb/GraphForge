import { API_BASE_URL, ApiError, UNAUTHORIZED_EVENT } from "./client";
import { apiFetch } from "./client";
import type {
  AIAnalysis,
  AIAnalysisResult,
  InvestigationResult,
  PublishReviewResult,
  PullRequestAnalysis,
} from "../../types/analysis";

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

/**
 * Runs the Change Investigation Agent - the agent decides which evidence is
 * worth gathering before producing the same AIAnalysisResult shape, plus a
 * `reasoning_log` explaining every decision it made along the way.
 */
export function investigatePullRequest(
  token: string,
  pullRequestId: string,
  model?: string,
): Promise<InvestigationResult> {
  return apiFetch<InvestigationResult>(`/pull-requests/${pullRequestId}/investigate`, {
    method: "POST",
    token,
    body: { model: model ?? null },
  });
}

/**
 * Publishes the already-computed AI analysis (from runAiAnalysis or
 * investigatePullRequest) as a comment on the corresponding GitHub pull
 * request. Never re-invokes the LLM - just formats and posts what's
 * already stored.
 */
export function publishReview(token: string, pullRequestId: string): Promise<PublishReviewResult> {
  return apiFetch<PublishReviewResult>(`/pull-requests/${pullRequestId}/publish-review`, {
    method: "POST",
    token,
  });
}

/**
 * Fetches the standalone HTML executive dashboard for a PR's most recently
 * persisted AI analysis. Returns raw HTML text rather than JSON, so this
 * bypasses `apiFetch` (which always parses the body as JSON) and does its
 * own auth-header/error handling instead.
 */
export async function getReviewReportHtml(token: string, pullRequestId: string): Promise<string> {
  const response = await fetch(
    `${API_BASE_URL}/pull-requests/${pullRequestId}/review-report?format=html`,
    { headers: { Authorization: `Bearer ${token}` } },
  );

  if (!response.ok) {
    const parsed = await response.json().catch(() => null);
    const code = parsed?.error?.code ?? "unknown_error";
    if (code === "invalid_token") {
      window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
    }
    throw new ApiError(
      response.status,
      code,
      parsed?.error?.message ?? `Request to fetch the review report failed with status ${response.status}.`,
    );
  }

  return response.text();
}
