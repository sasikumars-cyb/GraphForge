/**
 * API functions for the Workflow endpoints.
 */

import { apiFetch } from "./client";
import type {
  ContinueWorkflowResponse,
  CreateWorkflowRequest,
  OverrideStageResultRequest,
  WorkflowApprovalResponse,
  WorkflowDetail,
  WorkflowListResponse,
} from "../../types/agent";

export function createWorkflow(
  token: string,
  request: CreateWorkflowRequest,
): Promise<ContinueWorkflowResponse> {
  return apiFetch<ContinueWorkflowResponse>("/workflows", {
    method: "POST",
    token,
    body: request,
  });
}

export function getWorkflow(
  token: string,
  workflowId: string,
  signal?: AbortSignal,
): Promise<WorkflowDetail> {
  return apiFetch<WorkflowDetail>(`/workflows/${encodeURIComponent(workflowId)}`, {
    token,
    signal,
  });
}

export interface ListWorkflowsParams {
  page?: number;
  page_size?: number;
  status?: string;
  workflow_type?: string;
}

export function listWorkflows(
  token: string,
  params: ListWorkflowsParams = {},
  signal?: AbortSignal,
): Promise<WorkflowListResponse> {
  const searchParams = new URLSearchParams();
  if (params.page) searchParams.set("page", String(params.page));
  if (params.page_size) searchParams.set("page_size", String(params.page_size));
  if (params.status) searchParams.set("status", params.status);
  if (params.workflow_type) searchParams.set("workflow_type", params.workflow_type);

  const qs = searchParams.toString();
  return apiFetch<WorkflowListResponse>(`/workflows${qs ? `?${qs}` : ""}`, { token, signal });
}

export function continueWorkflow(
  token: string,
  workflowId: string,
  model?: string,
): Promise<ContinueWorkflowResponse> {
  return apiFetch<ContinueWorkflowResponse>(
    `/workflows/${encodeURIComponent(workflowId)}/continue`,
    {
      method: "POST",
      token,
      body: { model: model ?? null },
    },
  );
}

export function approveWorkflow(
  token: string,
  workflowId: string,
): Promise<WorkflowApprovalResponse> {
  return apiFetch<WorkflowApprovalResponse>(
    `/workflows/${encodeURIComponent(workflowId)}/approve`,
    { method: "POST", token },
  );
}

export function rejectWorkflow(
  token: string,
  workflowId: string,
): Promise<WorkflowApprovalResponse> {
  return apiFetch<WorkflowApprovalResponse>(`/workflows/${encodeURIComponent(workflowId)}/reject`, {
    method: "POST",
    token,
  });
}

export function deleteWorkflow(token: string, workflowId: string): Promise<undefined> {
  return apiFetch<undefined>(`/workflows/${encodeURIComponent(workflowId)}`, {
    method: "DELETE",
    token,
  });
}

/** Human correction on a completed stage's result — the mechanism behind
 * Context Explorer's review/edit UI. `override` is a partial dict (only the
 * fields the human actually changed); the backend merges it on top of that
 * stage's own AgentStep.result at read time, so downstream stages (e.g.
 * Planning reading context_discovery) see the corrected view. Named
 * generically since any stage a downstream consumer reads via
 * get_stage_result() can be corrected the same way, not just
 * context_discovery. */
export function overrideStageResult(
  token: string,
  workflowId: string,
  stage: string,
  request: OverrideStageResultRequest,
): Promise<WorkflowApprovalResponse> {
  return apiFetch<WorkflowApprovalResponse>(
    `/workflows/${encodeURIComponent(workflowId)}/stages/${encodeURIComponent(stage)}/override`,
    {
      method: "PATCH",
      token,
      body: request,
    },
  );
}

export function cancelWorkflow(
  token: string,
  workflowId: string,
): Promise<WorkflowApprovalResponse> {
  return apiFetch<WorkflowApprovalResponse>(`/workflows/${encodeURIComponent(workflowId)}/cancel`, {
    method: "POST",
    token,
  });
}
