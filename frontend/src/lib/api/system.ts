import { apiFetch } from "./client";

export interface ProviderStatus {
  name: string;
  configured: boolean;
  active: boolean;
  model: string | null;
}

export interface ConnectionStatus {
  name: string;
  status: "connected" | "configured" | "not_configured";
  detail: string | null;
}

export interface KnowledgeBaseStatus {
  repositories_tracked: number;
  repositories_indexed: number;
  repositories_pending: number;
}

export interface SystemStatusResponse {
  platform_status: "healthy" | "degraded" | "error";
  environment: string;
  version: string;
  ai_provider: ProviderStatus;
  ai_providers: ProviderStatus[];
  connections: ConnectionStatus[];
  knowledge_base: KnowledgeBaseStatus;
}

export function getSystemStatus(
  token: string,
  signal?: AbortSignal,
): Promise<SystemStatusResponse> {
  return apiFetch<SystemStatusResponse>("/system/status", { token, signal });
}
