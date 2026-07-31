import { apiFetch } from "./client";

export interface GoogleDriveConnectionStatus {
  connected: boolean;
  google_email: string | null;
  connected_at: string | null;
}

export function getGoogleDriveConnectionStatus(
  token: string,
  signal?: AbortSignal,
): Promise<GoogleDriveConnectionStatus> {
  return apiFetch<GoogleDriveConnectionStatus>("/google-drive/connection", { token, signal });
}

export function getGoogleDriveConnectAuthorizationUrl(
  token: string,
): Promise<{ authorization_url: string }> {
  return apiFetch<{ authorization_url: string }>("/google-drive/connect", { token });
}

export function disconnectGoogleDrive(token: string): Promise<undefined> {
  return apiFetch<undefined>("/google-drive/connection", { method: "DELETE", token });
}
