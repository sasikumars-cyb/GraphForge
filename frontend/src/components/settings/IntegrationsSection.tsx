import { useEffect, useState } from "react";
import {
  GitBranch,
  Database,
  FileText,
  MessageSquare,
  FolderOpen,
  Cloud,
  Plus,
  Loader2,
  Trash2,
  type LucideIcon,
} from "lucide-react";
import { Card } from "../Card";
import { StatusBadge, type StatusTone } from "../StatusBadge";
import { GitHubIntegrationCard } from "../GitHubIntegrationCard";
import { GoogleDriveIntegrationCard } from "../GoogleDriveIntegrationCard";
import { TestCaseUploadsCard } from "../TestCaseUploadsCard";
import { useAuth } from "../../app/auth-context";
import { ApiError } from "../../lib/api/client";
import {
  getKnowledgeOverview,
  createConnection,
  deleteConnection,
  checkConnectionHealth,
  type KnowledgeSourceInfo,
  type ConnectionInfo,
} from "../../lib/api/knowledge";
import {
  listTestRailProjects,
  syncTestRailProject,
  type TestRailProject,
} from "../../lib/api/testrail";

function messageFrom(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback;
}

// ---------------------------------------------------------------------------
// Icon resolver
// ---------------------------------------------------------------------------

const ICON_MAP: Record<string, LucideIcon> = {
  GitBranch,
  Database,
  FileText,
  MessageSquare,
  FolderOpen,
  Cloud,
};

function SourceIcon({ name }: { name: string }) {
  const Icon = ICON_MAP[name] ?? Database;
  return <Icon className="h-4 w-4" />;
}

// ---------------------------------------------------------------------------
// Status helpers
// ---------------------------------------------------------------------------

function connStatusTone(status: string): StatusTone {
  switch (status) {
    case "healthy":
      return "success";
    case "unconfigured":
      return "neutral";
    case "offline":
    case "auth_failed":
    case "unavailable":
    case "permission_denied":
      return "danger";
    case "rate_limited":
      return "warning";
    default:
      return "neutral";
  }
}

function connStatusLabel(status: string): string {
  switch (status) {
    case "healthy":
      return "Healthy";
    case "unconfigured":
      return "Unconfigured";
    case "offline":
      return "Offline";
    case "auth_failed":
      return "Auth failed";
    case "unavailable":
      return "Unavailable";
    case "rate_limited":
      return "Rate limited";
    case "permission_denied":
      return "Permission denied";
    case "unknown":
      return "Not tested";
    default:
      return status;
  }
}

function formatTime(iso: string | null): string {
  if (!iso) return "Never";
  const d = new Date(iso);
  const now = new Date();
  const diffMin = Math.floor((now.getTime() - d.getTime()) / 60000);
  if (diffMin < 1) return "Just now";
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.floor(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;
  return d.toLocaleDateString();
}

// ---------------------------------------------------------------------------
// Connection Row
// ---------------------------------------------------------------------------

/** TestRail's connection has an extra step no other generic source needs:
 * picking which project(s) to sync into the graph (see
 * app.services.testrail_service \u2014 no persisted "tracked project" concept,
 * always a live list from TestRail itself). Rendered inline under the
 * TestRail ConnectionRow, not a parallel custom card \u2014 the connect step
 * itself stays fully generic. */
function TestRailProjectsPanel() {
  const { token } = useAuth();
  const [projects, setProjects] = useState<TestRailProject[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [syncingId, setSyncingId] = useState<number | null>(null);

  async function load() {
    if (!token) return;
    setLoading(true);
    try {
      setProjects(await listTestRailProjects(token));
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't load TestRail projects.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  async function handleSync(project: TestRailProject) {
    if (!token) return;
    setSyncingId(project.id);
    try {
      await syncTestRailProject(token, project.id, project.name);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't start the sync.");
    } finally {
      setSyncingId(null);
    }
  }

  return (
    <div className="mt-2 rounded-md border border-line-muted bg-canvas p-3">
      {loading ? (
        <p className="text-xs text-fg-muted">Loading TestRail projects\u2026</p>
      ) : error ? (
        <p className="text-xs text-danger-fg">{error}</p>
      ) : !projects || projects.length === 0 ? (
        <p className="text-xs text-fg-muted">No projects found in this TestRail account.</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {projects.map((project) => {
            const isSyncing = syncingId === project.id || project.last_sync_status === "running";
            return (
              <li
                key={project.id}
                className="flex items-center justify-between gap-3 border-b border-line-muted pb-2 last:border-0 last:pb-0"
              >
                <div className="min-w-0">
                  <p className="truncate text-xs font-medium text-fg-secondary">{project.name}</p>
                  <p className="text-[11px] text-fg-muted">
                    {project.last_sync_status === "completed"
                      ? `Synced${project.case_count != null ? ` \u00B7 ${project.case_count} cases` : ""}${project.last_synced_at ? ` \u00B7 ${formatTime(project.last_synced_at)}` : ""}`
                      : project.last_sync_status === "failed"
                        ? "Last sync failed"
                        : project.last_sync_status === "running"
                          ? "Sync in progress\u2026"
                          : "Not synced yet"}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => void handleSync(project)}
                  disabled={isSyncing}
                  className="shrink-0 rounded-md border border-line px-2 py-1 text-[11px] font-medium text-fg-secondary hover:bg-surface-raised disabled:opacity-40"
                >
                  {isSyncing ? "Syncing\u2026" : "Sync"}
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

function ConnectionRow({
  conn,
  onHealthCheck,
  onDelete,
}: {
  conn: ConnectionInfo;
  onHealthCheck: (id: string) => Promise<void>;
  onDelete: (id: string) => Promise<void>;
}) {
  const [checking, setChecking] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [showProjects, setShowProjects] = useState(false);

  return (
    <div className="py-2.5">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium text-fg-secondary">{conn.name}</span>
            <StatusBadge label={connStatusLabel(conn.status)} tone={connStatusTone(conn.status)} />
          </div>
          <p className="mt-0.5 text-xs text-fg-muted">
            {conn.credentials_configured ? "Credentials configured" : "No credentials"}
            {conn.last_sync_at && ` \u00B7 Last sync ${formatTime(conn.last_sync_at)}`}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {conn.source_type === "testrail" && (
            <button
              type="button"
              onClick={() => setShowProjects(!showProjects)}
              className="rounded-md border border-line px-2.5 py-1 text-xs font-medium text-fg-secondary hover:bg-surface-raised"
            >
              Manage Projects
            </button>
          )}
          <button
            type="button"
            onClick={async () => {
              setChecking(true);
              await onHealthCheck(conn.id);
              setChecking(false);
            }}
            disabled={checking}
            className="rounded-md border border-line px-2.5 py-1 text-xs font-medium text-fg-secondary hover:bg-surface-raised disabled:opacity-40"
          >
            {checking ? "Testing..." : "Test Connection"}
          </button>
          <button
            type="button"
            onClick={async () => {
              setDeleting(true);
              await onDelete(conn.id);
              setDeleting(false);
            }}
            disabled={deleting}
            className="text-fg-muted hover:text-danger-fg disabled:opacity-40"
            title="Remove connection"
          >
            <Trash2 className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>
      {showProjects && <TestRailProjectsPanel />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Add Connection Form (simplified — no transport/MCP exposure)
// ---------------------------------------------------------------------------

function AddConnectionForm({
  source,
  onCreated,
  onCancel,
}: {
  source: KnowledgeSourceInfo;
  onCreated: () => void;
  onCancel: () => void;
}) {
  const { token } = useAuth();
  const [name, setName] = useState("");
  const [fields, setFields] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Determine which fields to show based on the source's default transport.
  // The UI intentionally hides transport selection — it's an internal detail.
  const defaultTransport = source.transports[0];
  const defaultAuth = defaultTransport?.auth_methods[0] ?? "none";
  const authFields = defaultTransport?.auth_fields[defaultAuth] ?? [];

  // Fields that contain secrets (render as password inputs, send as credentials).
  const SECRET_FIELDS = new Set(["token", "api_token", "api_key", "password", "private_key"]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!token || !defaultTransport || !name.trim()) return;
    setSaving(true);
    setError(null);
    try {
      const config: Record<string, string> = {};
      const credentials: Record<string, string> = {};
      for (const [key, value] of Object.entries(fields)) {
        if (SECRET_FIELDS.has(key)) {
          credentials[key] = value;
        } else {
          config[key] = value;
        }
      }

      await createConnection(token, {
        source_type: source.key,
        name: name.trim(),
        transport: defaultTransport.transport,
        auth_method: defaultAuth,
        config,
        credentials,
      });
      onCreated();
    } catch (err) {
      setError(messageFrom(err, "Failed to add connection."));
    } finally {
      setSaving(false);
    }
  }

  return (
    <form
      onSubmit={(e) => void handleSubmit(e)}
      className="mt-3 space-y-3 border-t border-line-muted pt-3"
    >
      <label className="flex flex-col gap-1 text-sm">
        <span className="text-fg-muted">Connection Name</span>
        <input
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder={`e.g. Production ${source.label}`}
          required
          className="rounded-md border border-line bg-canvas px-3 py-2 text-fg focus:border-info-line"
        />
      </label>

      {authFields.map((fieldName) => (
        <label key={fieldName} className="flex flex-col gap-1 text-sm">
          <span className="text-fg-muted">
            {fieldName.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())}
          </span>
          <input
            type={SECRET_FIELDS.has(fieldName) ? "password" : "text"}
            value={fields[fieldName] ?? ""}
            onChange={(e) => setFields({ ...fields, [fieldName]: e.target.value })}
            placeholder={fieldName.replace(/_/g, " ")}
            className="rounded-md border border-line bg-canvas px-3 py-2 text-fg focus:border-info-line"
          />
        </label>
      ))}

      {error && <p className="rounded-md bg-danger-bg px-3 py-2 text-sm text-danger-fg">{error}</p>}

      <div className="flex gap-2">
        <button
          type="submit"
          disabled={saving || !name.trim()}
          className="rounded-md bg-info-solid px-3 py-1.5 text-xs font-semibold text-black hover:brightness-110 disabled:bg-info-bg"
        >
          {saving ? "Adding..." : "Add Connection"}
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="rounded-md border border-line px-3 py-1.5 text-xs text-fg-secondary hover:bg-surface-raised"
        >
          Cancel
        </button>
      </div>
    </form>
  );
}

// ---------------------------------------------------------------------------
// Integration Card
// ---------------------------------------------------------------------------

function IntegrationCard({
  source,
  connections,
  onRefresh,
}: {
  source: KnowledgeSourceInfo;
  connections: ConnectionInfo[];
  onRefresh: () => void;
}) {
  const { token } = useAuth();
  const [showAdd, setShowAdd] = useState(false);

  async function handleHealthCheck(connId: string) {
    if (!token) return;
    await checkConnectionHealth(token, connId);
    onRefresh();
  }

  async function handleDelete(connId: string) {
    if (!token) return;
    await deleteConnection(token, connId);
    onRefresh();
  }

  function handleAddClick() {
    setShowAdd(!showAdd);
  }

  const isComingSoon = !source.available;

  return (
    <div
      className={`rounded-lg border border-line-muted bg-surface px-4 py-3 ${isComingSoon ? "opacity-60" : ""}`}
    >
      {/* Source header */}
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          <div className="mt-0.5 flex h-8 w-8 items-center justify-center rounded-md bg-surface-raised text-fg-secondary">
            <SourceIcon name={source.icon} />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <p className="text-sm font-medium text-fg-secondary">{source.label}</p>
              {connections.length > 0 && (
                <span className="rounded-full bg-info-bg px-2 py-0.5 text-[10px] font-semibold text-info-fg">
                  {connections.length} {connections.length === 1 ? "connection" : "connections"}
                </span>
              )}
              {isComingSoon && (
                <span className="rounded-full bg-surface-raised px-2 py-0.5 text-[10px] font-medium text-fg-muted">
                  Coming soon
                </span>
              )}
            </div>
            <p className="text-xs text-fg-muted">{source.description}</p>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {source.capabilities.map((cap) => (
                <span
                  key={cap}
                  className="rounded bg-surface-raised px-1.5 py-0.5 text-[10px] font-mono text-fg-muted"
                >
                  {cap}
                </span>
              ))}
            </div>
          </div>
        </div>

        {!isComingSoon && (
          <button
            type="button"
            onClick={handleAddClick}
            className="flex items-center gap-1 rounded-md border border-line px-2.5 py-1 text-xs font-medium text-fg-secondary hover:bg-surface-raised"
          >
            <Plus className="h-3 w-3" />
            Add Connection
          </button>
        )}
      </div>

      {/* Connections list */}
      {connections.length > 0 && (
        <div className="mt-3 divide-y divide-line-muted border-t border-line-muted pt-1">
          {connections.map((conn) => (
            <ConnectionRow
              key={conn.id}
              conn={conn}
              onHealthCheck={handleHealthCheck}
              onDelete={handleDelete}
            />
          ))}
        </div>
      )}

      {/* Add form */}
      {showAdd && (
        <AddConnectionForm
          source={source}
          onCreated={() => {
            setShowAdd(false);
            onRefresh();
          }}
          onCancel={() => setShowAdd(false)}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main Section
// ---------------------------------------------------------------------------

export function IntegrationsSection() {
  const { token, user } = useAuth();
  const isAdmin = user?.role === "admin";
  const [sources, setSources] = useState<KnowledgeSourceInfo[]>([]);
  const [connections, setConnections] = useState<ConnectionInfo[]>([]);
  const [loading, setLoading] = useState(isAdmin);
  const [error, setError] = useState<string | null>(null);

  // Jira/Confluence/Neo4j/etc. connections are install-wide config, gated
  // admin-only on the backend (GET /knowledge/overview) — only fetch it for
  // admins so a regular user isn't sent a request that only ever 403s.
  async function load() {
    if (!token || !isAdmin) return;
    setLoading(true);
    try {
      const overview = await getKnowledgeOverview(token);
      setSources(overview.sources);
      setConnections(overview.connections);
      setError(null);
    } catch (err) {
      setError(messageFrom(err, "Failed to load integrations."));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, isAdmin]);

  // Summary
  const totalConnections = connections.length;
  const healthyConnections = connections.filter((c) => c.status === "healthy").length;
  // GitHub and Google Drive each get their own dedicated card below
  // (GitHubIntegrationCard, GoogleDriveIntegrationCard), backed by their
  // own per-user /github/* and /google-drive/* endpoints rather than the
  // admin-only Knowledge Connections API — every user manages their own
  // OAuth connections regardless of role, unlike the install-wide sources
  // here. Any OAuth-authenticated source is inherently per-user (it's one
  // human's consent, never an install-wide credential), so this list is
  // every *non-OAuth* source, not a special case for these two by name.
  const PER_USER_OAUTH_SOURCE_KEYS = new Set(["github", "google_drive"]);
  const genericFormSources = sources.filter((s) => !PER_USER_OAUTH_SOURCE_KEYS.has(s.key));

  return (
    <div className="flex flex-col gap-5">
      {/* Summary card — first, so the connector counts are the first thing
          visible on the page. Admin-only, like the data it summarizes. */}
      {isAdmin && !loading && !error && (
        <Card>
          <div className="grid grid-cols-3 gap-4">
            <div>
              <p className="text-xs text-fg-muted">Available</p>
              <p className="mt-0.5 text-lg font-semibold text-fg">
                {sources.filter((s) => s.available).length}
              </p>
            </div>
            <div>
              <p className="text-xs text-fg-muted">Connections</p>
              <p className="mt-0.5 text-lg font-semibold text-info-fg">{totalConnections}</p>
            </div>
            <div>
              <p className="text-xs text-fg-muted">Healthy</p>
              <p className="mt-0.5 text-lg font-semibold text-success-fg">{healthyConnections}</p>
            </div>
          </div>
        </Card>
      )}

      <GitHubIntegrationCard />
      <GoogleDriveIntegrationCard />
      <TestCaseUploadsCard />

      {isAdmin &&
        (loading ? (
          <div className="flex items-center gap-2 py-8 text-sm text-fg-muted">
            <Loader2 className="h-4 w-4 animate-spin" />
            Loading integrations...
          </div>
        ) : error ? (
          <div className="rounded-lg border border-danger-line/30 bg-danger-bg px-4 py-3 text-sm text-danger-fg">
            {error}
          </div>
        ) : (
          genericFormSources.map((source) => (
            <IntegrationCard
              key={source.key}
              source={source}
              connections={connections.filter((c) => c.source_type === source.key)}
              onRefresh={() => void load()}
            />
          ))
        ))}
    </div>
  );
}
