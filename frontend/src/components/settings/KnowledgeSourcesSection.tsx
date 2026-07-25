import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { GitBranch, Database, FileText, MessageSquare, FolderOpen, Cloud, Loader2 } from "lucide-react";
import { Card } from "../Card";
import { StatusBadge, type StatusTone } from "../StatusBadge";
import { useAuth } from "../../app/auth-context";
import { ApiError } from "../../lib/api/client";
import {
  disconnectGitHub,
  getConnectAuthorizationUrl,
  getConnectionStatus,
  listAvailableRepositories,
  saveSelectedRepositories,
} from "../../lib/api/github";
import type { AvailableRepository, GitHubConnectionStatus } from "../../types/github";

function messageFrom(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback;
}

// ---------------------------------------------------------------------------
// Knowledge Source Card (generic for all sources)
// ---------------------------------------------------------------------------

interface KnowledgeSourceProps {
  name: string;
  description: string;
  icon: React.ReactNode;
  status: "connected" | "available" | "coming_soon";
  statusDetail?: string;
  capabilities: string[];
  children?: React.ReactNode;
  onConfigure?: () => void;
}

function statusTone(status: string): StatusTone {
  switch (status) {
    case "connected":
      return "success";
    case "available":
      return "neutral";
    default:
      return "neutral";
  }
}

function statusText(status: string): string {
  switch (status) {
    case "connected":
      return "Connected";
    case "available":
      return "Available";
    case "coming_soon":
      return "Coming soon";
    default:
      return status;
  }
}

function KnowledgeSourceCard({
  name,
  description,
  icon,
  status,
  statusDetail,
  capabilities,
  children,
  onConfigure,
}: KnowledgeSourceProps) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900/40 px-4 py-3">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          <div className="mt-0.5 flex h-8 w-8 items-center justify-center rounded-md bg-slate-800 text-slate-300">
            {icon}
          </div>
          <div>
            <p className="text-sm font-medium text-slate-200">{name}</p>
            <p className="text-xs text-slate-500">{description}</p>
            {statusDetail && (
              <p className="mt-0.5 text-xs text-slate-400">{statusDetail}</p>
            )}
            <div className="mt-2 flex flex-wrap gap-1.5">
              {capabilities.map((cap) => (
                <span
                  key={cap}
                  className="rounded bg-slate-800 px-1.5 py-0.5 text-[10px] font-mono text-slate-400"
                >
                  {cap}
                </span>
              ))}
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <StatusBadge label={statusText(status)} tone={statusTone(status)} />
          {status !== "coming_soon" && onConfigure && (
            <button
              type="button"
              onClick={() => setExpanded(!expanded)}
              className="rounded-md border border-slate-700 px-2.5 py-1 text-xs font-medium text-slate-300 hover:bg-slate-800"
            >
              {expanded ? "Close" : "Configure"}
            </button>
          )}
        </div>
      </div>
      {expanded && children && <div className="mt-3 border-t border-slate-800 pt-3">{children}</div>}
    </div>
  );
}

// ---------------------------------------------------------------------------
// GitHub Knowledge Source (full implementation from existing card)
// ---------------------------------------------------------------------------

function GitHubKnowledgeSource() {
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
      setError(messageFrom(err, "Couldn't load GitHub status."));
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
      setSelectedIds(new Set(repos.filter((r) => r.is_selected).map((r) => r.provider_repo_id)));
    } catch (err) {
      setError(messageFrom(err, "Couldn't load repositories."));
    } finally {
      setIsLoadingRepos(false);
    }
  }

  useEffect(() => {
    void loadStatus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  useEffect(() => {
    const outcome = searchParams.get("github");
    if (!outcome) return;
    if (outcome === "connected") {
      setNotice("GitHub connected.");
      void loadStatus();
    } else if (outcome === "error") {
      setError("Connecting to GitHub failed.");
    }
    const next = new URLSearchParams(searchParams);
    next.delete("github");
    setSearchParams(next, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (status?.connected) void loadRepositories();
    else setAvailableRepos(null);
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
      setError(messageFrom(err, "Couldn't start connection."));
      setIsConnecting(false);
    }
  }

  async function handleDisconnect() {
    if (!token) return;
    await disconnectGitHub(token);
    setStatus({ connected: false, github_username: null, connected_at: null });
  }

  async function handleSaveSelection() {
    if (!token || !availableRepos) return;
    setIsSaving(true);
    try {
      const selected = availableRepos.filter((r) => selectedIds.has(r.provider_repo_id));
      await saveSelectedRepositories(token, selected);
      setNotice(`Tracking ${selected.length} ${selected.length === 1 ? "repository" : "repositories"}.`);
      await loadRepositories();
    } catch (err) {
      setError(messageFrom(err, "Couldn't save selection."));
    } finally {
      setIsSaving(false);
    }
  }

  const isConnected = status?.connected ?? false;

  return (
    <KnowledgeSourceCard
      name="GitHub"
      description="Pull requests, commits, code, and repository metadata"
      icon={<GitBranch className="h-4 w-4" />}
      status={isConnected ? "connected" : "available"}
      statusDetail={isConnected ? `@${status?.github_username}` : undefined}
      capabilities={["Repository Discovery", "Pull Request Metadata", "Commit History", "Code Search"]}
      onConfigure={() => {}}
    >
      {error && (
        <p className="mb-2 rounded-md bg-rose-500/10 px-3 py-2 text-sm text-rose-300">{error}</p>
      )}
      {notice && !error && (
        <p className="mb-2 rounded-md bg-emerald-500/10 px-3 py-2 text-sm text-emerald-300">{notice}</p>
      )}

      {!isConnected ? (
        <button
          type="button"
          onClick={() => void handleConnect()}
          disabled={isConnecting || isLoadingStatus}
          className="rounded-md bg-sky-500 px-3 py-1.5 text-xs font-semibold text-slate-950 hover:bg-sky-400 disabled:bg-sky-500/50"
        >
          {isConnecting ? "Connecting..." : "Connect GitHub"}
        </button>
      ) : (
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <p className="text-sm font-medium text-slate-200">Tracked Repositories</p>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => void handleSaveSelection()}
                disabled={isSaving || isLoadingRepos}
                className="rounded-md bg-sky-500 px-3 py-1.5 text-xs font-semibold text-slate-950 hover:bg-sky-400 disabled:bg-sky-500/50"
              >
                {isSaving ? "Saving..." : "Save"}
              </button>
              <button
                type="button"
                onClick={() => void handleDisconnect()}
                className="rounded-md border border-slate-700 px-3 py-1.5 text-xs text-slate-300 hover:bg-slate-800"
              >
                Disconnect
              </button>
            </div>
          </div>

          {isLoadingRepos ? (
            <p className="text-xs text-slate-500">Loading repositories...</p>
          ) : !availableRepos || availableRepos.length === 0 ? (
            <p className="text-xs text-slate-500">No repositories found.</p>
          ) : (
            <ul className="max-h-48 divide-y divide-slate-800 overflow-y-auto rounded-md border border-slate-800">
              {availableRepos.map((repo) => (
                <li key={repo.provider_repo_id}>
                  <label className="flex cursor-pointer items-center gap-3 px-3 py-2 hover:bg-slate-800/40">
                    <input
                      type="checkbox"
                      checked={selectedIds.has(repo.provider_repo_id)}
                      onChange={() => {
                        setSelectedIds((prev) => {
                          const next = new Set(prev);
                          if (next.has(repo.provider_repo_id)) next.delete(repo.provider_repo_id);
                          else next.add(repo.provider_repo_id);
                          return next;
                        });
                      }}
                      className="h-3.5 w-3.5 rounded border-slate-600 bg-slate-950 text-sky-500"
                    />
                    <span className="truncate text-sm text-slate-200">{repo.full_name}</span>
                  </label>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </KnowledgeSourceCard>
  );
}

// ---------------------------------------------------------------------------
// Main Section
// ---------------------------------------------------------------------------

export function KnowledgeSourcesSection() {
  return (
    <div className="flex flex-col gap-4">
      <GitHubKnowledgeSource />

      <KnowledgeSourceCard
        name="Neo4j Knowledge Graph"
        description="Architecture relationships, dependency graphs, cross-repo links"
        icon={<Database className="h-4 w-4" />}
        status="connected"
        statusDetail="Local instance"
        capabilities={["Dependency Graph", "Architecture Context", "Repository Graph", "Cross-Repository Links"]}
      />

      <KnowledgeSourceCard
        name="Jira"
        description="Issues, epics, sprint context, and project metadata"
        icon={<FileText className="h-4 w-4" />}
        status="coming_soon"
        capabilities={["Issue Search", "Sprint Context", "Epic Dependencies", "Project Metadata"]}
      />

      <KnowledgeSourceCard
        name="Confluence"
        description="Technical documentation, ADRs, runbooks, and design docs"
        icon={<MessageSquare className="h-4 w-4" />}
        status="coming_soon"
        capabilities={["Documentation Search", "ADR Search", "Runbooks", "Architecture Docs"]}
      />

      <KnowledgeSourceCard
        name="Filesystem"
        description="Local repository clones and workspace files"
        icon={<FolderOpen className="h-4 w-4" />}
        status="connected"
        statusDetail="Indexer clone root configured"
        capabilities={["Code Indexing", "File Discovery", "AST Parsing", "Language Detection"]}
      />

      <KnowledgeSourceCard
        name="S3 / Object Storage"
        description="Artifacts, build outputs, and large file storage"
        icon={<Cloud className="h-4 w-4" />}
        status="coming_soon"
        capabilities={["Artifact Storage", "Build Output Search", "Configuration Archive"]}
      />
    </div>
  );
}
