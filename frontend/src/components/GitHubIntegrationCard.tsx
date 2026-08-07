import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { GitBranch, Loader2, Plus } from "lucide-react";
import { OAuthAppCredentialFields } from "./OAuthAppCredentialFields";
import { RepositoryPickerList } from "./RepositoryPickerList";
import { StatusBadge } from "./StatusBadge";
import { useAuth } from "../app/auth-context";
import { ApiError } from "../lib/api/client";
import {
  connectWithPersonalAccessToken,
  disconnectGitHub,
  getConnectAuthorizationUrl,
  getConnectionStatus,
  listAvailableRepositories,
  saveSelectedRepositories,
} from "../lib/api/github";
import { createLocalRepository } from "../lib/api/repositories";
import type { AvailableRepository, GitHubConnectionStatus } from "../types/github";

function messageFrom(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback;
}

/** GitHub account connection (OAuth or PAT) plus local-folder repository
 * tracking, in one card — same collapsed-by-default shell every other
 * Knowledge Source card in Settings -> Integrations uses
 * (IntegrationsSection.tsx's IntegrationCard), with "+ Add Connection"
 * expanding to reveal both forms. Kept as its own component rather than
 * folded into that generic one because GitHub (and local repos, which
 * ride along with it here) is per-user data driven by the /github/* and
 * /repositories/local endpoints, not the admin-only install-wide
 * Knowledge Connections API the generic card reads from. */
export function GitHubIntegrationCard({ onSaved }: { onSaved?: () => void } = {}) {
  const { token } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();

  const [status, setStatus] = useState<GitHubConnectionStatus | null>(null);
  const [isLoadingStatus, setIsLoadingStatus] = useState(true);
  const [isConnecting, setIsConnecting] = useState(false);
  const [showAdd, setShowAdd] = useState(false);

  const [patInput, setPatInput] = useState("");
  const [isConnectingWithPat, setIsConnectingWithPat] = useState(false);

  const [localName, setLocalName] = useState("");
  const [localPath, setLocalPath] = useState("");
  const [isAddingLocal, setIsAddingLocal] = useState(false);

  const [availableRepos, setAvailableRepos] = useState<AvailableRepository[] | null>(null);
  const [isLoadingRepos, setIsLoadingRepos] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [isSaving, setIsSaving] = useState(false);

  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  async function loadStatus() {
    if (!token) return;
    setIsLoadingStatus(true);
    try {
      setStatus(await getConnectionStatus(token));
    } catch (err) {
      setError(messageFrom(err, "Couldn't load GitHub connection status."));
    } finally {
      setIsLoadingStatus(false);
    }
  }

  async function loadRepositories() {
    if (!token) return;
    setIsLoadingRepos(true);
    try {
      const repos = await listAvailableRepositories(token);
      setAvailableRepos(repos);
      setSelectedIds(
        new Set(repos.filter((repo) => repo.is_selected).map((repo) => repo.provider_repo_id)),
      );
    } catch (err) {
      setError(messageFrom(err, "Couldn't load repositories from GitHub."));
    } finally {
      setIsLoadingRepos(false);
    }
  }

  // Initial connection status, once.
  useEffect(() => {
    void loadStatus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  // The backend redirects here with ?github=connected|error after the OAuth
  // callback; consume it once, then strip it so a page refresh doesn't
  // re-show the notice.
  useEffect(() => {
    const outcome = searchParams.get("github");
    if (!outcome) return;

    if (outcome === "connected") {
      setNotice("GitHub connected.");
      void loadStatus();
    } else if (outcome === "error") {
      setError("Connecting to GitHub failed. Please try again.");
    }

    const next = new URLSearchParams(searchParams);
    next.delete("github");
    setSearchParams(next, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (status?.connected) {
      void loadRepositories();
    } else {
      setAvailableRepos(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status?.connected]);

  async function handleConnect() {
    if (!token) return;
    setIsConnecting(true);
    setError(null);
    try {
      const { authorization_url } = await getConnectAuthorizationUrl(token);
      window.location.href = authorization_url;
    } catch (err) {
      setError(messageFrom(err, "Couldn't start the GitHub connection."));
      setIsConnecting(false);
    }
  }

  async function handleConnectWithPat() {
    if (!token || !patInput.trim()) return;
    setIsConnectingWithPat(true);
    setError(null);
    try {
      const next = await connectWithPersonalAccessToken(token, patInput.trim());
      setStatus(next);
      setPatInput("");
      setShowAdd(false);
      setNotice(
        next.scope_warning
          ? `GitHub connected as @${next.github_username}. ${next.scope_warning}`
          : `GitHub connected as @${next.github_username}.`,
      );
    } catch (err) {
      setError(messageFrom(err, "Couldn't connect with this token."));
    } finally {
      setIsConnectingWithPat(false);
    }
  }

  async function handleAddLocalRepository() {
    if (!token || !localName.trim() || !localPath.trim()) return;
    setIsAddingLocal(true);
    setError(null);
    try {
      const repo = await createLocalRepository(token, {
        name: localName.trim(),
        path: localPath.trim(),
      });
      setNotice(`Tracking '${repo.name}' (branch: ${repo.default_branch}).`);
      setLocalName("");
      setLocalPath("");
      setShowAdd(false);
      onSaved?.();
    } catch (err) {
      setError(messageFrom(err, "Couldn't add this local repository."));
    } finally {
      setIsAddingLocal(false);
    }
  }

  async function handleDisconnect() {
    if (!token) return;
    setError(null);
    await disconnectGitHub(token);
    setStatus({
      connected: false,
      github_username: null,
      connected_at: null,
      auth_method: null,
      scope_warning: null,
    });
  }

  async function handleSaveSelection() {
    if (!token || !availableRepos) return;
    setIsSaving(true);
    setError(null);
    try {
      const selected = availableRepos.filter((repo) => selectedIds.has(repo.provider_repo_id));
      await saveSelectedRepositories(token, selected);
      setNotice(
        selected.length === 0
          ? "No repositories selected — tracking stopped for all of them."
          : `Tracking ${selected.length} ${selected.length === 1 ? "repository" : "repositories"}.`,
      );
      await loadRepositories();
      onSaved?.();
    } catch (err) {
      setError(messageFrom(err, "Couldn't save your repository selection."));
    } finally {
      setIsSaving(false);
    }
  }

  return (
    <div className="rounded-lg border border-line-muted bg-surface px-4 py-3">
      {/* Header — same shell as IntegrationsSection's generic IntegrationCard */}
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          <div className="mt-0.5 flex h-8 w-8 items-center justify-center rounded-md bg-surface-raised text-fg-secondary">
            <GitBranch className="h-4 w-4" aria-hidden="true" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <p className="text-sm font-medium text-fg-secondary">GitHub</p>
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
                ? `Connected as @${status.github_username}` +
                  (status.auth_method === "pat" ? " via personal access token" : "")
                : "Read pull requests from your repositories, or track a local folder"}
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
      {status?.connected && status.scope_warning && !error && !notice && (
        <p className="mt-3 rounded-md bg-warning-bg px-3 py-2 text-sm text-warning-fg">
          {status.scope_warning}
        </p>
      )}

      {/* Tracked GitHub repositories — visible whenever connected, same as
          the generic card's always-visible connections list. */}
      {status?.connected && (
        <div className="mt-3 rounded-lg border border-line-muted p-4">
          <div className="mb-3 flex items-center justify-between">
            <p className="text-sm font-medium text-fg-secondary">Repositories</p>
            <button
              type="button"
              onClick={() => void handleSaveSelection()}
              disabled={isSaving || isLoadingRepos || !availableRepos}
              className="rounded-md bg-info-solid px-3 py-1.5 text-xs font-semibold text-black hover:brightness-110 disabled:cursor-not-allowed disabled:bg-info-bg"
            >
              {isSaving ? "Saving…" : "Save selection"}
            </button>
          </div>

          {isLoadingRepos ? (
            <p className="text-sm text-fg-muted">Loading repositories from GitHub…</p>
          ) : !availableRepos || availableRepos.length === 0 ? (
            <p className="text-sm text-fg-muted">No repositories found for this GitHub account.</p>
          ) : (
            <RepositoryPickerList
              repos={availableRepos}
              selectedIds={selectedIds}
              onChangeSelected={setSelectedIds}
            />
          )}
        </div>
      )}

      {/* Add Connection — GitHub account (OAuth/PAT) and local folders */}
      {showAdd && (
        <div className="mt-3 flex flex-col gap-4 border-t border-line-muted pt-3">
          <div className="flex flex-col gap-2">
            <p className="text-xs font-medium uppercase tracking-wide text-fg-muted">
              Connect a GitHub account
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
              <span className="text-xs text-fg-muted">or paste a personal access token:</span>
            </div>
            <div className="flex gap-2">
              <input
                id="github-pat"
                aria-label="Personal access token"
                type="password"
                autoComplete="off"
                value={patInput}
                onChange={(e) => setPatInput(e.target.value)}
                placeholder="ghp_…"
                className="flex-1 rounded-md border border-line-strong bg-canvas px-3 py-1.5 text-xs text-fg-secondary"
              />
              <button
                type="button"
                onClick={() => void handleConnectWithPat()}
                disabled={isConnectingWithPat || !patInput.trim()}
                className="rounded-md bg-info-solid px-3 py-1.5 text-xs font-semibold text-black hover:brightness-110 disabled:cursor-not-allowed disabled:bg-info-bg"
              >
                {isConnectingWithPat ? "Connecting…" : "Connect with token"}
              </button>
            </div>
            <p className="text-xs text-fg-muted">
              Requires a token with <code>repo</code> and <code>read:user</code> scopes.
            </p>
            <OAuthAppCredentialFields providerKey="github" label="GitHub OAuth App (admin)" />
          </div>

          <div className="flex flex-col gap-2 border-t border-line-muted pt-3">
            <p className="text-xs font-medium uppercase tracking-wide text-fg-muted">
              Track a local folder
            </p>
            <p className="text-xs text-fg-muted">
              No GitHub account needed — indexes a folder on the server's filesystem directly.
            </p>
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              <input
                type="text"
                aria-label="Local repository name"
                value={localName}
                onChange={(e) => setLocalName(e.target.value)}
                placeholder="Name (e.g. order-service)"
                className="rounded-md border border-line-strong bg-canvas px-3 py-1.5 text-xs text-fg-secondary"
              />
              <input
                type="text"
                aria-label="Local repository path"
                value={localPath}
                onChange={(e) => setLocalPath(e.target.value)}
                placeholder="Path (relative to the local repos root)"
                className="rounded-md border border-line-strong bg-canvas px-3 py-1.5 text-xs text-fg-secondary"
              />
            </div>
            <button
              type="button"
              onClick={() => void handleAddLocalRepository()}
              disabled={isAddingLocal || !localName.trim() || !localPath.trim()}
              className="self-start rounded-md bg-info-solid px-3 py-1.5 text-xs font-semibold text-black hover:brightness-110 disabled:cursor-not-allowed disabled:bg-info-bg"
            >
              {isAddingLocal ? "Adding…" : "Add local repository"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
