/**
 * API client for the AI Workspace — providers, profiles, validation.
 */

import { apiFetch } from "./client";

// ---------------------------------------------------------------------------
// Types (mirror the backend DTOs)
// ---------------------------------------------------------------------------

export interface ModelInfo {
  id: string;
  label: string;
  context_window: number | null;
}

export interface ProviderInfo {
  key: string;
  label: string;
  implemented: boolean;
  notes: string;
  capabilities: string[];
  models: ModelInfo[];
  requires_api_key: boolean;
  default_model: string;
  configured: boolean;
  enabled: boolean;
  api_key_configured: boolean;
  model: string | null;
  base_url: string | null;
  temperature: number | null;
  max_tokens: number | null;
  status: string;
  status_detail: string | null;
  last_validated_at: string | null;
  last_success_at: string | null;
  latency_ms: number | null;
}

export interface ValidationResponse {
  provider_key: string;
  ok: boolean;
  status: string;
  model: string;
  latency_ms: number;
  message: string;
}

export interface ProviderUpsertRequest {
  api_key?: string | null;
  model?: string | null;
  base_url?: string | null;
  temperature?: number | null;
  max_tokens?: number | null;
  enabled?: boolean | null;
}

// ---------------------------------------------------------------------------
// API calls
// ---------------------------------------------------------------------------

export async function listProviders(token: string): Promise<ProviderInfo[]> {
  return apiFetch<ProviderInfo[]>("/ai/providers", { token });
}

export async function upsertProvider(
  token: string,
  providerKey: string,
  body: ProviderUpsertRequest,
): Promise<ProviderInfo> {
  return apiFetch<ProviderInfo>(`/ai/providers/${providerKey}`, {
    method: "PUT",
    token,
    body,
  });
}

export async function validateProvider(
  token: string,
  providerKey: string,
  model?: string | null,
): Promise<ValidationResponse> {
  const query = model ? `?model=${encodeURIComponent(model)}` : "";
  return apiFetch<ValidationResponse>(`/ai/providers/${providerKey}/validate${query}`, {
    method: "POST",
    token,
  });
}

export async function listProviderModels(
  token: string,
  providerKey: string,
): Promise<ModelInfo[]> {
  return apiFetch<ModelInfo[]>(`/ai/providers/${providerKey}/models`, { token });
}

// ---------------------------------------------------------------------------
// Workspace settings — default provider/model, stage mapping, fallback
// ---------------------------------------------------------------------------

export interface AIWorkspaceSettings {
  default_profile_slug: string | null;
  default_provider: string | null;
  default_model: string | null;
  temperature: number | null;
  max_tokens: number | null;
  stage_overrides: Record<string, unknown>;
  fallback_order: string[];
  fallback_enabled: boolean;
}

export async function getAISettings(token: string): Promise<AIWorkspaceSettings> {
  return apiFetch<AIWorkspaceSettings>("/ai/settings", { token });
}

export async function setDefaultProvider(
  token: string,
  providerKey: string,
  model?: string | null,
): Promise<AIWorkspaceSettings> {
  return apiFetch<AIWorkspaceSettings>("/ai/settings", {
    method: "PUT",
    token,
    body: { default_provider: providerKey, default_model: model ?? null },
  });
}
