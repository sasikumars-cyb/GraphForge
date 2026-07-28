import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { GitBranch, Loader2 } from "lucide-react";
import { Card } from "./Card";
import { StatusBadge } from "./StatusBadge";
import { useAuth } from "../app/auth-context";
import { ApiError } from "../lib/api/client";
import {
  disconnectGitHub,
  getConnectAuthorizationUrl,
  getConnectionStatus,
  listAvailableRepositories,
  saveSelectedRepositories,
} from "../lib/api/github";
import type { AvailableRepository, GitHubConnectionStatus } from "../types/github";

function messageFrom(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback;
}

export function GitHubIntegrationCard({ onSaved }: { onSaved?: () => void } = {}) {
  const { token } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();

  const [status, setStatus] = useState<GitHubConnectionStatus | null>(null);
  const [isLoadingStatus, setIsLoadingStatus] = useState(true);
  const [isConnecting, setIsConnecting] = useState(false);

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

  async function handleDisconnect() {
    if (!token) return;
    setError(null);
    await disconnectGitHub(token);
    setStatus({ connected: false, github_username: null, connected_at: null });
  }

  function toggleRepo(id: string) {
    setSelectedIds((previous) => {
      const next = new Set(previous);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
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
    <Card title="Integrations" description="Connect the systems GraphForge reads from">
      <div className="flex flex-col gap-4">
        <div className="flex items-center justify-between rounded-lg border border-slate-800 px-4 py-3">
          <div className="flex items-center gap-3">
            <GitBranch className="h-5 w-5 text-slate-300" aria-hidden="true" />
            <div>
              <p className="text-sm font-medium text-slate-200">GitHub</p>
              <p className="text-xs text-slate-500">
                {status?.connected
                  ? `Connected as @${status.github_username}`
                  : "Read pull requests from your repositories"}
              </p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            {isLoadingStatus ? (
              <Loader2 className="h-4 w-4 animate-spin text-slate-500" aria-hidden="true" />
            ) : (
              <StatusBadge
                label={status?.connected ? "Connected" : "Not connected"}
                tone={status?.connected ? "success" : "neutral"}
              />
            )}
            {status?.connected ? (
              <button
                type="button"
                onClick={() => void handleDisconnect()}
                className="rounded-md border border-slate-700 px-3 py-1.5 text-xs font-medium text-slate-300 hover:bg-slate-800"
              >
                Disconnect
              </button>
            ) : (
              <button
                type="button"
                onClick={() => void handleConnect()}
                disabled={isConnecting || isLoadingStatus}
                className="rounded-md bg-sky-500 px-3 py-1.5 text-xs font-semibold text-black hover:bg-sky-400 disabled:cursor-not-allowed disabled:bg-sky-500/50"
              >
                {isConnecting ? "Connecting…" : "Connect"}
              </button>
            )}
          </div>
        </div>

        {error && (
          <p role="alert" className="rounded-md bg-rose-500/10 px-3 py-2 text-sm text-rose-300">
            {error}
          </p>
        )}
        {notice && !error && (
          <p className="rounded-md bg-emerald-500/10 px-3 py-2 text-sm text-emerald-300">
            {notice}
          </p>
        )}

        {status?.connected && (
          <div className="rounded-lg border border-slate-800 p-4">
            <div className="mb-3 flex items-center justify-between">
              <p className="text-sm font-medium text-slate-200">Repositories</p>
              <button
                type="button"
                onClick={() => void handleSaveSelection()}
                disabled={isSaving || isLoadingRepos || !availableRepos}
                className="rounded-md bg-sky-500 px-3 py-1.5 text-xs font-semibold text-black hover:bg-sky-400 disabled:cursor-not-allowed disabled:bg-sky-500/50"
              >
                {isSaving ? "Saving…" : "Save selection"}
              </button>
            </div>

            {isLoadingRepos ? (
              <p className="text-sm text-slate-500">Loading repositories from GitHub…</p>
            ) : !availableRepos || availableRepos.length === 0 ? (
              <p className="text-sm text-slate-500">
                No repositories found for this GitHub account.
              </p>
            ) : (
              <ul className="max-h-72 divide-y divide-slate-800 overflow-y-auto">
                {availableRepos.map((repo) => (
                  <li key={repo.provider_repo_id}>
                    <label className="flex cursor-pointer items-center gap-3 py-2">
                      <input
                        type="checkbox"
                        checked={selectedIds.has(repo.provider_repo_id)}
                        onChange={() => toggleRepo(repo.provider_repo_id)}
                        className="h-4 w-4 rounded border-slate-600 bg-slate-950 text-sky-500 focus:ring-sky-500"
                      />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-sm text-slate-200">
                          {repo.full_name}
                        </span>
                      </span>
                      {repo.private && <StatusBadge label="Private" tone="neutral" />}
                    </label>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </div>
    </Card>
  );
}
