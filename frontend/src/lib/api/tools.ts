import { apiFetch } from "./client";
import type {
  ConfigureToolRequest,
  HealthCheckResult,
  Tool,
} from "../../types/tools";

export function listTools(token: string): Promise<Tool[]> {
  return apiFetch<Tool[]>("/tools", { token });
}

export function getTool(token: string, toolId: string): Promise<Tool> {
  return apiFetch<Tool>(`/tools/${encodeURIComponent(toolId)}`, { token });
}

export function checkToolHealth(token: string, toolId: string): Promise<HealthCheckResult> {
  return apiFetch<HealthCheckResult>(`/tools/${encodeURIComponent(toolId)}/health`, {
    method: "POST",
    token,
    body: {},
  });
}

export function configureTool(
  token: string,
  toolId: string,
  body: ConfigureToolRequest,
): Promise<Tool> {
  return apiFetch<Tool>(`/tools/${encodeURIComponent(toolId)}`, {
    method: "PUT",
    token,
    body,
  });
}
