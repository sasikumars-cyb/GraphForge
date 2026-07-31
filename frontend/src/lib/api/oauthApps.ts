import { apiFetch } from "./client";

export interface OAuthAppCredentialStatus {
  provider_key: string;
  configured: boolean;
  // "database" | "environment" | "unset"
  source: string;
  client_id: string | null;
}

export function listOAuthAppCredentials(token: string): Promise<OAuthAppCredentialStatus[]> {
  return apiFetch<OAuthAppCredentialStatus[]>("/oauth-apps", { token });
}

export function updateOAuthAppCredential(
  token: string,
  providerKey: string,
  clientId: string,
  clientSecret: string,
): Promise<OAuthAppCredentialStatus> {
  return apiFetch<OAuthAppCredentialStatus>(`/oauth-apps/${providerKey}`, {
    method: "PUT",
    token,
    body: { client_id: clientId, client_secret: clientSecret },
  });
}

export function clearOAuthAppCredential(
  token: string,
  providerKey: string,
): Promise<OAuthAppCredentialStatus> {
  return apiFetch<OAuthAppCredentialStatus>(`/oauth-apps/${providerKey}`, {
    method: "DELETE",
    token,
  });
}
