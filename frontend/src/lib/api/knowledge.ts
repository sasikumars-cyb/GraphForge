/**
 * API client for Knowledge Sources — source catalog and connection CRUD.
 */

import { apiFetch } from "./client";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface TransportInfo {
  transport: string;
  label: string;
  auth_methods: string[];
  auth_fields: Record<string, string[]>;
}

export interface KnowledgeSourceInfo {
  key: string;
  label: string;
  icon: string;
  description: string;
  capabilities: string[];
  transports: TransportInfo[];
  available: boolean;
  connection_count: number;
}

export interface ConnectionInfo {
  id: string;
  source_type: string;
  name: string;
  transport: string;
  auth_method: string;
  config: Record<string, unknown>;
  scope: Record<string, unknown>;
  enabled: boolean;
  credentials_configured: boolean;
  status: string;
  status_detail: string | null;
  last_sync_at: string | null;
  last_success_at: string | null;
  latency_ms: number | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface KnowledgeOverview {
  sources: KnowledgeSourceInfo[];
  connections: ConnectionInfo[];
}

export interface ConnectionCreateRequest {
  source_type: string;
  name: string;
  transport: string;
  auth_method: string;
  config?: Record<string, unknown>;
  credentials?: Record<string, unknown>;
  scope?: Record<string, unknown>;
}

export interface ConnectionUpdateRequest {
  name?: string;
  transport?: string;
  auth_method?: string;
  config?: Record<string, unknown>;
  credentials?: Record<string, unknown>;
  scope?: Record<string, unknown>;
  enabled?: boolean;
}

export interface ConnectionHealthResponse {
  id: string;
  status: string;
  status_detail: string | null;
  latency_ms: number | null;
}

// ---------------------------------------------------------------------------
// API calls
// ---------------------------------------------------------------------------

export function getKnowledgeOverview(token: string): Promise<KnowledgeOverview> {
  return apiFetch<KnowledgeOverview>("/knowledge/overview", { token });
}

export function listKnowledgeSources(token: string): Promise<KnowledgeSourceInfo[]> {
  return apiFetch<KnowledgeSourceInfo[]>("/knowledge/sources", { token });
}

export function listConnections(
  token: string,
  sourceType?: string,
): Promise<ConnectionInfo[]> {
  const query = sourceType ? `?source_type=${encodeURIComponent(sourceType)}` : "";
  return apiFetch<ConnectionInfo[]>(`/knowledge/connections${query}`, { token });
}

export function createConnection(
  token: string,
  body: ConnectionCreateRequest,
): Promise<ConnectionInfo> {
  return apiFetch<ConnectionInfo>("/knowledge/connections", {
    method: "POST",
    token,
    body,
  });
}

export function updateConnection(
  token: string,
  connectionId: string,
  body: ConnectionUpdateRequest,
): Promise<ConnectionInfo> {
  return apiFetch<ConnectionInfo>(`/knowledge/connections/${connectionId}`, {
    method: "PUT",
    token,
    body,
  });
}

export function deleteConnection(token: string, connectionId: string): Promise<void> {
  return apiFetch<void>(`/knowledge/connections/${connectionId}`, {
    method: "DELETE",
    token,
  });
}

export function checkConnectionHealth(
  token: string,
  connectionId: string,
): Promise<ConnectionHealthResponse> {
  return apiFetch<ConnectionHealthResponse>(
    `/knowledge/connections/${connectionId}/health`,
    { method: "POST", token, body: {} },
  );
}
