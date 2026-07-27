import { apiFetch } from "./client";

export interface JiraIssueResult {
  key: string;
  summary: string;
  status: string;
  issue_type: string;
  url: string;
}

export function searchJiraIssues(
  token: string,
  query: string,
  signal?: AbortSignal,
): Promise<JiraIssueResult[]> {
  return apiFetch<JiraIssueResult[]>(`/jira/search?q=${encodeURIComponent(query)}`, {
    token,
    signal,
  });
}
