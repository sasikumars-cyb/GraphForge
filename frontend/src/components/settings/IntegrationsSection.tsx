import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
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
import { getConnectAuthorizationUrl } from "../../lib/api/github";

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

  return (
    <div className="flex items-center justify-between gap-3 py-2.5">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium text-slate-200">{conn.name}</span>
          <StatusBadge label={connStatusLabel(conn.status)} tone={connStatusTone(conn.status)} />
        </div>
        <p className="mt-0.5 text-xs text-slate-500">
          {conn.credentials_configured ? "Credentials configured" : "No credentials"}
          {conn.last_sync_at && ` \u00B7 Last sync ${formatTime(conn.last_sync_at)}`}
        </p>
      </div>
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={async () => {
            setChecking(true);
            await onHealthCheck(conn.id);
            setChecking(false);
          }}
          disabled={checking}
          className="rounded-md border border-slate-700 px-2.5 py-1 text-xs font-medium text-slate-300 hover:bg-slate-800 disabled:opacity-40"
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
          className="text-slate-500 hover:text-rose-400 disabled:opacity-40"
          title="Remove connection"
        >
          <Trash2 className="h-3.5 w-3.5" />
        </button>
      </div>
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
    <form onSubmit={(e) => void handleSubmit(e)} className="mt-3 space-y-3 border-t border-slate-800 pt-3">
      <label className="flex flex-col gap-1 text-sm">
        <span className="text-slate-400">Connection Name</span>
        <input
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder={`e.g. Production ${source.label}`}
          required
          className="rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-slate-100 outline-none focus:border-sky-500"
        />
      </label>

      {authFields.map((fieldName) => (
        <label key={fieldName} className="flex flex-col gap-1 text-sm">
          <span className="text-slate-400">
            {fieldName.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())}
          </span>
          <input
            type={SECRET_FIELDS.has(fieldName) ? "password" : "text"}
            value={fields[fieldName] ?? ""}
            onChange={(e) => setFields({ ...fields, [fieldName]: e.target.value })}
            placeholder={fieldName.replace(/_/g, " ")}
            className="rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-slate-100 outline-none focus:border-sky-500"
          />
        </label>
      ))}

      {error && (
        <p className="rounded-md bg-rose-500/10 px-3 py-2 text-sm text-rose-300">{error}</p>
      )}

      <div className="flex gap-2">
        <button
          type="submit"
          disabled={saving || !name.trim()}
          className="rounded-md bg-sky-500 px-3 py-1.5 text-xs font-semibold text-slate-950 hover:bg-sky-400 disabled:bg-sky-500/50"
        >
          {saving ? "Adding..." : "Add Connection"}
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="rounded-md border border-slate-700 px-3 py-1.5 text-xs text-slate-300 hover:bg-slate-800"
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
  const [connectingOAuth, setConnectingOAuth] = useState(false);
  const [oauthError, setOauthError] = useState<string | null>(null);

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

  // GitHub's REST transport lists OAuth first with no form fields — it's
  // handled by a real OAuth redirect, not the generic connection form.
  const usesOAuthRedirect = source.key === "github";

  async function handleAddClick() {
    if (!usesOAuthRedirect) {
      setShowAdd(!showAdd);
      return;
    }
    if (!token) return;
    setConnectingOAuth(true);
    setOauthError(null);
    try {
      const { authorization_url } = await getConnectAuthorizationUrl(token);
      window.location.href = authorization_url;
    } catch (err) {
      setOauthError(messageFrom(err, "Couldn't start the GitHub connection."));
      setConnectingOAuth(false);
    }
  }

  const isComingSoon = !source.available;

  return (
    <div className={`rounded-lg border border-slate-800 bg-slate-900/40 px-4 py-3 ${isComingSoon ? "opacity-60" : ""}`}>
      {/* Source header */}
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          <div className="mt-0.5 flex h-8 w-8 items-center justify-center rounded-md bg-slate-800 text-slate-300">
            <SourceIcon name={source.icon} />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <p className="text-sm font-medium text-slate-200">{source.label}</p>
              {connections.length > 0 && (
                <span className="rounded-full bg-sky-500/10 px-2 py-0.5 text-[10px] font-semibold text-sky-300">
                  {connections.length} {connections.length === 1 ? "connection" : "connections"}
                </span>
              )}
              {isComingSoon && (
                <span className="rounded-full bg-slate-800 px-2 py-0.5 text-[10px] font-medium text-slate-500">
                  Coming soon
                </span>
              )}
            </div>
            <p className="text-xs text-slate-500">{source.description}</p>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {source.capabilities.map((cap) => (
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

        {!isComingSoon && (
          <button
            type="button"
            onClick={() => void handleAddClick()}
            disabled={connectingOAuth}
            className="flex items-center gap-1 rounded-md border border-slate-700 px-2.5 py-1 text-xs font-medium text-slate-300 hover:bg-slate-800 disabled:opacity-40"
          >
            <Plus className="h-3 w-3" />
            {connectingOAuth ? "Redirecting..." : "Add Connection"}
          </button>
        )}
      </div>

      {oauthError && (
        <p className="mt-2 rounded-md bg-rose-500/10 px-3 py-2 text-xs text-rose-300">
          {oauthError}
        </p>
      )}

      {/* Connections list */}
      {connections.length > 0 && (
        <div className="mt-3 divide-y divide-slate-800/60 border-t border-slate-800 pt-1">
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
  const { token } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const [sources, setSources] = useState<KnowledgeSourceInfo[]>([]);
  const [connections, setConnections] = useState<ConnectionInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  async function load() {
    if (!token) return;
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
  }, [token]);

  // The backend redirects here with ?github=connected|error after the OAuth
  // callback (GitHub can't be reached via XHR — see AddConnectAuthorization
  // flow in the card above). Consume it once, then strip it so a page
  // refresh doesn't re-show the notice.
  useEffect(() => {
    const outcome = searchParams.get("github");
    if (!outcome) return;

    if (outcome === "connected") {
      setNotice("GitHub connected.");
      void load();
    } else if (outcome === "error") {
      setError("Connecting to GitHub failed. Please try again.");
    }

    const next = new URLSearchParams(searchParams);
    next.delete("github");
    setSearchParams(next, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (loading) {
    return (
      <div className="flex items-center gap-2 py-8 text-sm text-slate-500">
        <Loader2 className="h-4 w-4 animate-spin" />
        Loading integrations...
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded-lg border border-rose-500/30 bg-rose-500/5 px-4 py-3 text-sm text-rose-300">
        {error}
      </div>
    );
  }

  // Summary
  const totalConnections = connections.length;
  const healthyConnections = connections.filter((c) => c.status === "healthy").length;

  return (
    <div className="flex flex-col gap-5">
      {notice && (
        <div className="rounded-lg border border-emerald-500/30 bg-emerald-500/5 px-4 py-3 text-sm text-emerald-300">
          {notice}
        </div>
      )}

      {/* Summary card */}
      <Card>
        <div className="grid grid-cols-3 gap-4">
          <div>
            <p className="text-xs text-slate-500">Available</p>
            <p className="mt-0.5 text-lg font-semibold text-slate-100">
              {sources.filter((s) => s.available).length}
            </p>
          </div>
          <div>
            <p className="text-xs text-slate-500">Connections</p>
            <p className="mt-0.5 text-lg font-semibold text-sky-400">{totalConnections}</p>
          </div>
          <div>
            <p className="text-xs text-slate-500">Healthy</p>
            <p className="mt-0.5 text-lg font-semibold text-emerald-400">{healthyConnections}</p>
          </div>
        </div>
      </Card>

      {/* Integration cards */}
      {sources.map((source) => (
        <IntegrationCard
          key={source.key}
          source={source}
          connections={connections.filter((c) => c.source_type === source.key)}
          onRefresh={() => void load()}
        />
      ))}
    </div>
  );
}
