import { apiFetch } from "./client";

export interface ExternalContextProviderConfig {
  base_url?: string;
  email?: string;
  api_token?: string;
  api_key?: string;
  project_key?: string;
  project_id?: string;
  section_id?: string;
  service_account_json?: string;
  client_credentials?: string;
  file_ids?: string[];
}

export interface ExternalContextSettingsPayload {
  providers: Record<string, ExternalContextProviderConfig>;
}

export async function getExternalContextSettings(token?: string): Promise<ExternalContextSettingsPayload> {
  return apiFetch<ExternalContextSettingsPayload>("/external-context", { token });
}

export async function saveExternalContextSettings(
  payload: ExternalContextSettingsPayload,
  token?: string,
): Promise<{ ok: boolean }> {
  return apiFetch<{ ok: boolean }>("/external-context", {
    method: "PUT",
    body: payload,
    token,
  });
}
