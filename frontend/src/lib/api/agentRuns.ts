/**
 * API functions for the Agent Runs endpoints.
 * Follows the existing apiFetch convention from client.ts.
 */

import { apiFetch } from "./client";
import type {
  AgentManifest,
  CreateRunRequest,
  CreateRunResponse,
  RunDetail,
  RunListResponse,
} from "../../types/agent";

export function createAgentRun(
  token: string,
  request: CreateRunRequest,
): Promise<CreateRunResponse> {
  return apiFetch<CreateRunResponse>("/agent-runs", {
    method: "POST",
    token,
    body: request,
  });
}

export function getAgentRun(
  token: string,
  runId: string,
  signal?: AbortSignal,
): Promise<RunDetail> {
  return apiFetch<RunDetail>(`/agent-runs/${encodeURIComponent(runId)}`, { token, signal });
}

export interface CancelRunResponse {
  run_id: string;
  status: string;
}

export function cancelAgentRun(token: string, runId: string): Promise<CancelRunResponse> {
  return apiFetch<CancelRunResponse>(`/agent-runs/${encodeURIComponent(runId)}/cancel`, {
    method: "POST",
    token,
  });
}

export function deleteAgentRun(token: string, runId: string): Promise<undefined> {
  return apiFetch<undefined>(`/agent-runs/${encodeURIComponent(runId)}`, {
    method: "DELETE",
    token,
  });
}

export interface ListRunsParams {
  page?: number;
  page_size?: number;
  goal?: string;
  status?: string;
  subject_type?: string;
}

export function listAgentRuns(
  token: string,
  params: ListRunsParams = {},
  signal?: AbortSignal,
): Promise<RunListResponse> {
  const searchParams = new URLSearchParams();
  if (params.page) searchParams.set("page", String(params.page));
  if (params.page_size) searchParams.set("page_size", String(params.page_size));
  if (params.goal) searchParams.set("goal", params.goal);
  if (params.status) searchParams.set("status", params.status);
  if (params.subject_type) searchParams.set("subject_type", params.subject_type);

  const qs = searchParams.toString();
  return apiFetch<RunListResponse>(`/agent-runs${qs ? `?${qs}` : ""}`, { token, signal });
}

export function listAgentManifests(token: string): Promise<AgentManifest[]> {
  return apiFetch<AgentManifest[]>("/agent-runs/agents/manifests", { token });
}
