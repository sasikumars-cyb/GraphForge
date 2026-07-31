import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Cloud, Loader2, Plus } from "lucide-react";
import { OAuthAppCredentialFields } from "./OAuthAppCredentialFields";
import { StatusBadge } from "./StatusBadge";
import { useAuth } from "../app/auth-context";
import { ApiError } from "../lib/api/client";
import {
  disconnectGoogleDrive,
  getGoogleDriveConnectAuthorizationUrl,
  getGoogleDriveConnectionStatus,
  type GoogleDriveConnectionStatus,
} from "../lib/api/googleDrive";

function messageFrom(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback;
}

/** Google Drive account connection (OAuth, read-only) — same collapsed
 * shell + "+ Add Connection" expand pattern every card in Settings ->
 * Integrations uses (GitHubIntegrationCard, the generic IntegrationCard).
 * Its own dedicated component (not the generic one) for the same reason
 * GitHubIntegrationCard is: Drive access is a per-user OAuth connection
 * driven by /google-drive/* endpoints, not the admin-only install-wide
 * Knowledge Connections API. Unlike GitHub, there's no repo-style "pick
 * what to track" step — once connected, a Drive link just resolves
 * wherever it's pasted into a task description (see backend
 * app.context_pipeline.reasoning.investigators.GoogleDriveInvestigator),
 * so the expand panel here is connect-only. */
export function GoogleDriveIntegrationCard() {
  const { token } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();

  const [status, setStatus] = useState<GoogleDriveConnectionStatus | null>(null);
  const [isLoadingStatus, setIsLoadingStatus] = useState(true);
  const [isConnecting, setIsConnecting] = useState(false);
  const [showAdd, setShowAdd] = useState(false);

  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  async function loadStatus() {
    if (!token) return;
    setIsLoadingStatus(true);
    try {
      setStatus(await getGoogleDriveConnectionStatus(token));
    } catch (err) {
      setError(messageFrom(err, "Couldn't load Google Drive connection status."));
    } finally {
      setIsLoadingStatus(false);
    }
  }

  useEffect(() => {
    void loadStatus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  // The backend redirects here with ?google_drive=connected|error after
  // the OAuth callback (can't be reached via XHR) — same convention
  // GitHubIntegrationCard uses for its own ?github= param.
  useEffect(() => {
    const outcome = searchParams.get("google_drive");
    if (!outcome) return;

    if (outcome === "connected") {
      setNotice("Google Drive connected.");
      void loadStatus();
    } else if (outcome === "error") {
      setError("Connecting to Google Drive failed. Please try again.");
    }

    const next = new URLSearchParams(searchParams);
    next.delete("google_drive");
    setSearchParams(next, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleConnect() {
    if (!token) return;
    setIsConnecting(true);
    setError(null);
    try {
      const { authorization_url } = await getGoogleDriveConnectAuthorizationUrl(token);
      window.location.href = authorization_url;
    } catch (err) {
      setError(messageFrom(err, "Couldn't start the Google Drive connection."));
      setIsConnecting(false);
    }
  }

  async function handleDisconnect() {
    if (!token) return;
    setError(null);
    await disconnectGoogleDrive(token);
    setStatus({ connected: false, google_email: null, connected_at: null });
  }

  return (
    <div className="rounded-lg border border-line-muted bg-surface px-4 py-3">
      {/* Header — same shell as GitHubIntegrationCard / the generic IntegrationCard */}
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          <div className="mt-0.5 flex h-8 w-8 items-center justify-center rounded-md bg-surface-raised text-fg-secondary">
            <Cloud className="h-4 w-4" aria-hidden="true" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <p className="text-sm font-medium text-fg-secondary">Google Drive</p>
              {isLoadingStatus ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin text-fg-muted" aria-hidden="true" />
              ) : (
                <StatusBadge
                  label={status?.connected ? "Connected" : "Not connected"}
                  tone={status?.connected ? "success" : "neutral"}
                />
              )}
            </div>
            <p className="text-xs text-fg-muted">
              {status?.connected
                ? `Connected as ${status.google_email}`
                : "Paste a Drive file/folder link into a task description to pull it in as context"}
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          {status?.connected && (
            <button
              type="button"
              onClick={() => void handleDisconnect()}
              className="rounded-md border border-line px-2.5 py-1 text-xs font-medium text-fg-secondary hover:bg-surface-raised"
            >
              Disconnect
            </button>
          )}
          <button
            type="button"
            onClick={() => setShowAdd(!showAdd)}
            className="flex items-center gap-1 rounded-md border border-line px-2.5 py-1 text-xs font-medium text-fg-secondary hover:bg-surface-raised"
          >
            <Plus className="h-3 w-3" />
            Add Connection
          </button>
        </div>
      </div>

      {error && (
        <p role="alert" className="mt-3 rounded-md bg-danger-bg px-3 py-2 text-sm text-danger-fg">
          {error}
        </p>
      )}
      {notice && !error && (
        <p className="mt-3 rounded-md bg-success-bg px-3 py-2 text-sm text-success-fg">{notice}</p>
      )}

      {/* Add Connection — Google Drive account (OAuth only, no fields to
          fill: unlike Jira/TestRail there's no API key to paste, and
          unlike GitHub there's no PAT alternative Google supports here). */}
      {showAdd && (
        <div className="mt-3 flex flex-col gap-2 border-t border-line-muted pt-3">
          <p className="text-xs font-medium uppercase tracking-wide text-fg-muted">
            Connect a Google account
          </p>
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={() => void handleConnect()}
              disabled={isConnecting}
              className="rounded-md bg-info-solid px-3 py-1.5 text-xs font-semibold text-black hover:brightness-110 disabled:cursor-not-allowed disabled:bg-info-bg"
            >
              {isConnecting ? "Connecting…" : "Connect via OAuth"}
            </button>
            <span className="text-xs text-fg-muted">
              Read-only access to your Drive — no separate credentials needed.
            </span>
          </div>
          <OAuthAppCredentialFields
            providerKey="google_drive"
            label="Google OAuth Client (admin)"
          />
        </div>
      )}
    </div>
  );
}
