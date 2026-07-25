/**
 * API functions for the Workflow endpoints.
 */

import { apiFetch } from "./client";
import type {
  ContinueWorkflowResponse,
  CreateWorkflowRequest,
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
): Promise<WorkflowListResponse> {
  const searchParams = new URLSearchParams();
  if (params.page) searchParams.set("page", String(params.page));
  if (params.page_size) searchParams.set("page_size", String(params.page_size));
  if (params.status) searchParams.set("status", params.status);
  if (params.workflow_type) searchParams.set("workflow_type", params.workflow_type);

  const qs = searchParams.toString();
  return apiFetch<WorkflowListResponse>(`/workflows${qs ? `?${qs}` : ""}`, { token });
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

export function cancelWorkflow(
  token: string,
  workflowId: string,
): Promise<WorkflowApprovalResponse> {
  return apiFetch<WorkflowApprovalResponse>(`/workflows/${encodeURIComponent(workflowId)}/cancel`, {
    method: "POST",
    token,
  });
}
