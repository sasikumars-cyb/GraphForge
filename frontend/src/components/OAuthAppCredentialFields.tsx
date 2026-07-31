import { useEffect, useState } from "react";
import { useAuth } from "../app/auth-context";
import { ApiError } from "../lib/api/client";
import {
  clearOAuthAppCredential,
  listOAuthAppCredentials,
  updateOAuthAppCredential,
  type OAuthAppCredentialStatus,
} from "../lib/api/oauthApps";

function messageFrom(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback;
}

function sourceLabel(status: OAuthAppCredentialStatus): string {
  if (status.source === "database") return "Configured here";
  if (status.source === "environment") return "Configured via environment variable";
  return "Not configured";
}

interface OAuthAppCredentialFieldsProps {
  providerKey: string;
  label: string;
}

/** Admin-only panel for the app-level OAuth Client ID/Secret a provider
 * (GitHub, Google Drive) needs for every user's "Connect via OAuth" to
 * work at all — distinct from a user's own per-user connection above it.
 * Renders nothing for non-admins: this sets a credential shared by the
 * whole installation, not something any user should be able to overwrite.
 * Previously the only way to set this was `backend/.env` + a container
 * restart (see docs/setup.md); this is the UI alternative. */
export function OAuthAppCredentialFields({ providerKey, label }: OAuthAppCredentialFieldsProps) {
  const { token, user } = useAuth();
  const isAdmin = user?.role === "admin";

  const [status, setStatus] = useState<OAuthAppCredentialStatus | null>(null);
  const [clientId, setClientId] = useState("");
  const [clientSecret, setClientSecret] = useState("");
  const [isSaving, setIsSaving] = useState(false);
  const [isClearing, setIsClearing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  async function load() {
    if (!token || !isAdmin) return;
    try {
      const list = await listOAuthAppCredentials(token);
      setStatus(list.find((s) => s.provider_key === providerKey) ?? null);
    } catch (err) {
      setError(messageFrom(err, "Couldn't load OAuth app credential status."));
    }
  }

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, isAdmin]);

  async function handleSave() {
    if (!token || !clientId.trim() || !clientSecret.trim()) return;
    setIsSaving(true);
    setError(null);
    setNotice(null);
    try {
      const updated = await updateOAuthAppCredential(token, providerKey, clientId, clientSecret);
      setStatus(updated);
      setClientId("");
      setClientSecret("");
      setNotice("Saved. Users can now connect via OAuth.");
    } catch (err) {
      setError(messageFrom(err, "Couldn't save these credentials."));
    } finally {
      setIsSaving(false);
    }
  }

  async function handleClear() {
    if (!token) return;
    setIsClearing(true);
    setError(null);
    setNotice(null);
    try {
      const updated = await clearOAuthAppCredential(token, providerKey);
      setStatus(updated);
      setNotice("Cleared.");
    } catch (err) {
      setError(messageFrom(err, "Couldn't clear these credentials."));
    } finally {
      setIsClearing(false);
    }
  }

  if (!isAdmin) return null;

  return (
    <div className="mt-3 flex flex-col gap-2 rounded-md border border-line-muted bg-canvas/40 p-3">
      <div className="flex items-center justify-between gap-2">
        <p className="text-xs font-medium uppercase tracking-wide text-fg-muted">{label}</p>
        {status && <span className="text-xs text-fg-muted">{sourceLabel(status)}</span>}
      </div>

      <label className="flex flex-col gap-1 text-xs">
        <span className="text-fg-muted">Client ID</span>
        <input
          type="text"
          value={clientId}
          onChange={(e) => setClientId(e.target.value)}
          placeholder={status?.client_id ?? "Client ID"}
          className="rounded-md border border-line-strong bg-canvas px-3 py-1.5 text-xs text-fg-secondary"
        />
      </label>
      <label className="flex flex-col gap-1 text-xs">
        <span className="text-fg-muted">Client Secret</span>
        <input
          type="password"
          value={clientSecret}
          onChange={(e) => setClientSecret(e.target.value)}
          placeholder="Client secret"
          className="rounded-md border border-line-strong bg-canvas px-3 py-1.5 text-xs text-fg-secondary"
        />
      </label>

      {error && (
        <p role="alert" className="rounded-md bg-danger-bg px-3 py-2 text-xs text-danger-fg">
          {error}
        </p>
      )}
      {notice && !error && (
        <p className="rounded-md bg-success-bg px-3 py-2 text-xs text-success-fg">{notice}</p>
      )}

      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={() => void handleSave()}
          disabled={isSaving || !clientId.trim() || !clientSecret.trim()}
          className="rounded-md bg-info-solid px-3 py-1.5 text-xs font-semibold text-black hover:brightness-110 disabled:cursor-not-allowed disabled:bg-info-bg"
        >
          {isSaving ? "Saving…" : "Save"}
        </button>
        {status?.source === "database" && (
          <button
            type="button"
            onClick={() => void handleClear()}
            disabled={isClearing}
            className="rounded-md border border-line px-3 py-1.5 text-xs font-medium text-fg-secondary hover:bg-surface-raised"
          >
            {isClearing ? "Clearing…" : "Clear"}
          </button>
        )}
      </div>
    </div>
  );
}
