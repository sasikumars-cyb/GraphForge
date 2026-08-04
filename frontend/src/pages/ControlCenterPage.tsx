import { useQuery } from "@tanstack/react-query";
import { useAuth } from "../app/auth-context";
import { getSystemStatus } from "../lib/api/system";
import { getConnectionStatus } from "../lib/api/github";
import {
  Activity,
  Brain,
  CheckCircle2,
  CircleDot,
  Database,
  GitBranch,
  Globe,
  Server,
  Sparkles,
  XCircle,
} from "lucide-react";

export function ControlCenterPage() {
  const { token } = useAuth();

  // KAN-37 — first page migrated to the shared TanStack Query layer.
  // Two independent queries, not one combined queryFn, deliberately
  // preserves the original Promise.allSettled asymmetry: a failed
  // system-status fetch is a real error (nothing else on this page can
  // render without it), but a failed GitHub connection check degrades
  // silently to "not connected" - it was never load-bearing for the
  // rest of the page and the original code never surfaced its failure
  // either.
  const systemQuery = useQuery({
    queryKey: ["system-status"],
    queryFn: ({ signal }) => getSystemStatus(token as string, signal),
    enabled: token !== null,
  });
  const githubQuery = useQuery({
    queryKey: ["github-connection-status"],
    queryFn: ({ signal }) => getConnectionStatus(token as string, signal),
    enabled: token !== null,
  });

  const system = systemQuery.data ?? null;
  const github = githubQuery.data ?? null;
  const error = systemQuery.isError ? "Failed to load platform status." : null;
  const loading = systemQuery.isPending || githubQuery.isPending;

  const platformHealthLabel =
    system?.platform_status === "healthy"
      ? "All systems operational"
      : system?.platform_status === "degraded"
        ? "Degraded — check configuration"
        : "Error — platform needs attention";

  const platformHealthColor =
    system?.platform_status === "healthy"
      ? "text-success-fg"
      : system?.platform_status === "degraded"
        ? "text-warning-fg"
        : "text-danger-fg";

  if (loading) {
    return (
      <div className="flex flex-col gap-6">
        <div>
          <h2 className="text-2xl font-bold tracking-tight text-fg">Control Center</h2>
          <p className="mt-1 text-sm text-fg-muted">Loading platform status…</p>
        </div>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          {[1, 2, 3, 4].map((i) => (
            <div key={i} className="h-16 animate-pulse rounded-xl bg-surface-raised" />
          ))}
        </div>
      </div>
    );
  }

  if (error && !system) {
    return (
      <div className="flex flex-col gap-6">
        <div>
          <h2 className="text-2xl font-bold tracking-tight text-fg">Control Center</h2>
        </div>
        <div className="rounded-lg border border-danger-line/30 bg-danger-bg px-4 py-3 text-sm text-danger-fg">
          {error}
        </div>
      </div>
    );
  }

  if (!system) return null;

  const activeProvider = system.ai_providers.find((p) => p.active);
  const githubConnected = github?.connected ?? false;
  const connectedCount =
    system.connections.filter((c) => c.name !== "GitHub" && c.status !== "not_configured").length +
    (githubConnected ? 1 : 0);

  return (
    <div className="flex flex-col gap-6">
      {/* ── Header ──────────────────────────────────────────────── */}
      <div>
        <h2 className="text-2xl font-bold tracking-tight text-fg">Control Center</h2>
        <p className={`mt-1 text-sm ${platformHealthColor}`}>
          <span className="inline-block h-2 w-2 rounded-full bg-current mr-2" />
          {platformHealthLabel}
        </p>
      </div>

      {/* ── Status Indicators ───────────────────────────────────── */}
      {/* 4 columns only from `lg` up — at `sm` (640px) each of 4 columns
          has too little width for its value line, so "development" /
          "bedrock" truncated to "d…" / "b…" well before the page felt
          cramped generally (reproduced at 800–1024px). 2 columns until
          there's genuinely enough room per card removes that dead zone. */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatusIndicator
          label="Platform"
          value={system.environment}
          status={system.platform_status === "healthy" ? "ok" : "warn"}
          icon={Server}
        />
        <StatusIndicator
          label="AI Provider"
          value={activeProvider?.name ?? "none"}
          status={activeProvider?.configured ? "ok" : "error"}
          icon={Brain}
        />
        <StatusIndicator
          label="Connections"
          value={`${connectedCount} of ${system.connections.length}`}
          status={connectedCount > 0 ? "ok" : "neutral"}
          icon={GitBranch}
        />
        <StatusIndicator
          label="Knowledge Base"
          value={`${system.knowledge_base.repositories_indexed} indexed`}
          status={system.knowledge_base.repositories_indexed > 0 ? "ok" : "neutral"}
          icon={Database}
        />
      </div>

      {/* ── Two-column grid ─────────────────────────────────────── */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {/* Left: AI Providers */}
        <section className="flex flex-col gap-2">
          <h3 className="text-xs font-semibold tracking-wide text-fg-muted uppercase">
            AI Providers
          </h3>
          <div className="divide-y divide-line-muted rounded-lg border border-line-muted bg-surface">
            {system.ai_providers.map((provider) => (
              <div key={provider.name} className="flex items-center gap-3 px-4 py-2.5">
                {provider.active ? (
                  <CheckCircle2 className="h-4 w-4 shrink-0 text-success-fg" aria-hidden="true" />
                ) : provider.configured ? (
                  <CircleDot className="h-4 w-4 shrink-0 text-fg-muted" aria-hidden="true" />
                ) : (
                  <XCircle className="h-4 w-4 shrink-0 text-fg-subtle" aria-hidden="true" />
                )}
                <span className="flex-1 truncate text-sm text-fg-secondary capitalize">
                  {provider.name}
                </span>
                {provider.active && provider.model && (
                  <span
                    className="max-w-[40%] truncate rounded bg-accent-bg px-2 py-0.5 text-xs font-medium text-accent-fg ring-1 ring-inset ring-accent-line/20"
                    title={provider.model}
                  >
                    {provider.model}
                  </span>
                )}
                <span className="shrink-0 text-xs text-fg-subtle">
                  {provider.active
                    ? "active"
                    : provider.configured
                      ? "configured"
                      : "not configured"}
                </span>
              </div>
            ))}
          </div>
        </section>

        {/* Right: Connections */}
        <section className="flex flex-col gap-2">
          <h3 className="text-xs font-semibold tracking-wide text-fg-muted uppercase">
            Connections
          </h3>
          <div className="divide-y divide-line-muted rounded-lg border border-line-muted bg-surface">
            {/* GitHub (from live check) */}
            <div className="flex items-center gap-3 px-4 py-2.5">
              {githubConnected ? (
                <CheckCircle2 className="h-4 w-4 shrink-0 text-success-fg" aria-hidden="true" />
              ) : (
                <CircleDot className="h-4 w-4 shrink-0 text-fg-subtle" aria-hidden="true" />
              )}
              <GitBranch className="h-3.5 w-3.5 shrink-0 text-fg-muted" aria-hidden="true" />
              <span className="flex-1 text-sm text-fg-secondary">GitHub</span>
              {githubConnected && github?.github_username && (
                <span className="text-xs text-fg-muted">@{github.github_username}</span>
              )}
              <ConnectionBadge status={githubConnected ? "connected" : "not_configured"} />
            </div>

            {/* Other connections from system API */}
            {system.connections
              .filter((c) => c.name !== "GitHub")
              .map((conn) => (
                <div key={conn.name} className="flex items-center gap-3 px-4 py-2.5">
                  {conn.status === "connected" ? (
                    <CheckCircle2 className="h-4 w-4 shrink-0 text-success-fg" aria-hidden="true" />
                  ) : conn.status === "configured" ? (
                    <CircleDot className="h-4 w-4 shrink-0 text-accent-fg" aria-hidden="true" />
                  ) : (
                    <XCircle className="h-4 w-4 shrink-0 text-fg-subtle" aria-hidden="true" />
                  )}
                  <ConnectionIcon name={conn.name} />
                  <span className="flex-1 text-sm text-fg-secondary">{conn.name}</span>
                  {conn.detail && (
                    <span className="max-w-[160px] truncate text-xs text-fg-subtle">
                      {conn.detail}
                    </span>
                  )}
                  <ConnectionBadge status={conn.status} />
                </div>
              ))}
          </div>
        </section>
      </div>

      {/* ── Knowledge Base & Platform Info ──────────────────────── */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {/* Left: Knowledge Base */}
        <section className="flex flex-col gap-2">
          <h3 className="text-xs font-semibold tracking-wide text-fg-muted uppercase">
            Knowledge Base
          </h3>
          <div className="rounded-lg border border-line-muted bg-surface px-4 py-3">
            <div className="space-y-2.5">
              <MetricRow
                label="Repositories tracked"
                value={system.knowledge_base.repositories_tracked}
              />
              <MetricRow
                label="Repositories indexed"
                value={system.knowledge_base.repositories_indexed}
                highlight={system.knowledge_base.repositories_indexed > 0}
              />
              {system.knowledge_base.repositories_pending > 0 && (
                <MetricRow
                  label="Indexing in progress"
                  value={system.knowledge_base.repositories_pending}
                  highlight
                />
              )}
              {system.knowledge_base.repositories_graph_missing > 0 && (
                <MetricRow
                  label="Graph missing (needs re-index)"
                  value={system.knowledge_base.repositories_graph_missing}
                  highlight
                />
              )}
            </div>
          </div>
        </section>

        {/* Right: Platform Info */}
        <section className="flex flex-col gap-2">
          <h3 className="text-xs font-semibold tracking-wide text-fg-muted uppercase">Platform</h3>
          <div className="rounded-lg border border-line-muted bg-surface px-4 py-3">
            <div className="space-y-2.5">
              <MetricRow label="Version" value={system.version} />
              <MetricRow label="Environment" value={system.environment} />
              <MetricRow
                label="Active AI"
                value={
                  activeProvider
                    ? `${activeProvider.name}${activeProvider.model ? ` · ${activeProvider.model}` : ""}`
                    : "None"
                }
              />
            </div>
          </div>
        </section>
      </div>
    </div>
  );
}

// ── Sub-components ─────────────────────────────────────────────────

function StatusIndicator({
  label,
  value,
  status,
  icon: Icon,
}: {
  label: string;
  value: string;
  status: "ok" | "warn" | "error" | "neutral";
  icon: React.ComponentType<{ className?: string }>;
}) {
  const dotColor = {
    ok: "bg-success-solid",
    warn: "bg-warning-solid",
    error: "bg-danger-solid",
    neutral: "bg-line-strong",
  }[status];

  return (
    <div className="flex items-center gap-3 rounded-xl border border-line-muted bg-surface px-4 py-3">
      <Icon className="h-4 w-4 shrink-0 text-fg-muted" aria-hidden="true" />
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium text-fg-secondary">{value}</p>
        <p className="text-xs text-fg-subtle">{label}</p>
      </div>
      <span className={`h-2 w-2 shrink-0 rounded-full ${dotColor}`} />
    </div>
  );
}

function ConnectionBadge({ status }: { status: string }) {
  if (status === "connected") {
    return (
      <span className="rounded bg-success-bg px-1.5 py-0.5 text-[10px] font-medium text-success-fg ring-1 ring-inset ring-success-line/20">
        connected
      </span>
    );
  }
  if (status === "configured") {
    return (
      <span className="rounded bg-accent-bg px-1.5 py-0.5 text-[10px] font-medium text-accent-fg ring-1 ring-inset ring-accent-line/20">
        configured
      </span>
    );
  }
  return (
    <span className="rounded bg-surface-raised px-1.5 py-0.5 text-[10px] font-medium text-fg-subtle ring-1 ring-inset ring-line">
      not configured
    </span>
  );
}

function ConnectionIcon({ name }: { name: string }) {
  const className = "h-3.5 w-3.5 shrink-0 text-fg-muted";
  switch (name) {
    case "Neo4j":
      return <Globe className={className} aria-hidden="true" />;
    case "PostgreSQL":
      return <Database className={className} aria-hidden="true" />;
    case "Jira":
      return <Activity className={className} aria-hidden="true" />;
    default:
      return <Sparkles className={className} aria-hidden="true" />;
  }
}

function MetricRow({
  label,
  value,
  highlight,
}: {
  label: string;
  value: string | number;
  highlight?: boolean;
}) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-sm text-fg-muted">{label}</span>
      <span
        className={`text-sm font-medium tabular-nums ${highlight ? "text-accent-fg" : "text-fg-secondary"}`}
      >
        {value}
      </span>
    </div>
  );
}
