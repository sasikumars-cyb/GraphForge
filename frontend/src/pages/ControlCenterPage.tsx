import { useEffect, useState } from "react";
import { useAuth } from "../app/auth-context";
import { getSystemStatus } from "../lib/api/system";
import { getConnectionStatus } from "../lib/api/github";
import type { SystemStatusResponse } from "../lib/api/system";
import type { GitHubConnectionStatus } from "../types/github";
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
  const [system, setSystem] = useState<SystemStatusResponse | null>(null);
  const [github, setGithub] = useState<GitHubConnectionStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!token) return;
    setLoading(true);
    Promise.allSettled([
      getSystemStatus(token),
      getConnectionStatus(token),
    ]).then(([sysResult, ghResult]) => {
      if (sysResult.status === "fulfilled") {
        setSystem(sysResult.value);
      } else {
        setError("Failed to load platform status.");
      }
      if (ghResult.status === "fulfilled") {
        setGithub(ghResult.value);
      }
      setLoading(false);
    });
  }, [token]);

  const platformHealthLabel =
    system?.platform_status === "healthy"
      ? "All systems operational"
      : system?.platform_status === "degraded"
        ? "Degraded — check configuration"
        : "Error — platform needs attention";

  const platformHealthColor =
    system?.platform_status === "healthy"
      ? "text-emerald-400"
      : system?.platform_status === "degraded"
        ? "text-amber-400"
        : "text-rose-400";

  if (loading) {
    return (
      <div className="flex flex-col gap-6">
        <div>
          <h2 className="text-2xl font-bold tracking-tight text-slate-50">Control Center</h2>
          <p className="mt-1 text-sm text-slate-500">Loading platform status…</p>
        </div>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          {[1, 2, 3, 4].map((i) => (
            <div key={i} className="h-16 animate-pulse rounded-xl bg-slate-800/40" />
          ))}
        </div>
      </div>
    );
  }

  if (error && !system) {
    return (
      <div className="flex flex-col gap-6">
        <div>
          <h2 className="text-2xl font-bold tracking-tight text-slate-50">Control Center</h2>
        </div>
        <div className="rounded-lg border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-300">
          {error}
        </div>
      </div>
    );
  }

  if (!system) return null;

  const activeProvider = system.ai_providers.find((p) => p.active);
  const githubConnected = github?.connected ?? false;

  return (
    <div className="flex flex-col gap-6">
      {/* ── Header ──────────────────────────────────────────────── */}
      <div>
        <h2 className="text-2xl font-bold tracking-tight text-slate-50">Control Center</h2>
        <p className={`mt-1 text-sm ${platformHealthColor}`}>
          <span className="inline-block h-2 w-2 rounded-full bg-current mr-2" />
          {platformHealthLabel}
        </p>
      </div>

      {/* ── Status Indicators ───────────────────────────────────── */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
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
          label="GitHub"
          value={githubConnected ? "Connected" : "Not linked"}
          status={githubConnected ? "ok" : "neutral"}
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
          <h3 className="text-xs font-semibold tracking-wide text-slate-500 uppercase">
            AI Providers
          </h3>
          <div className="divide-y divide-slate-800/40 rounded-lg border border-slate-800/60 bg-slate-900/30">
            {system.ai_providers.map((provider) => (
              <div
                key={provider.name}
                className="flex items-center gap-3 px-4 py-2.5"
              >
                {provider.active ? (
                  <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-500" aria-hidden="true" />
                ) : provider.configured ? (
                  <CircleDot className="h-4 w-4 shrink-0 text-slate-500" aria-hidden="true" />
                ) : (
                  <XCircle className="h-4 w-4 shrink-0 text-slate-700" aria-hidden="true" />
                )}
                <span className="flex-1 text-sm text-slate-300 capitalize">{provider.name}</span>
                {provider.active && provider.model && (
                  <span className="rounded bg-brand-500/10 px-2 py-0.5 text-xs font-medium text-brand-300 ring-1 ring-inset ring-brand-500/20">
                    {provider.model}
                  </span>
                )}
                <span className="text-xs text-slate-600">
                  {provider.active ? "active" : provider.configured ? "configured" : "not configured"}
                </span>
              </div>
            ))}
          </div>
        </section>

        {/* Right: Connections */}
        <section className="flex flex-col gap-2">
          <h3 className="text-xs font-semibold tracking-wide text-slate-500 uppercase">
            Connections
          </h3>
          <div className="divide-y divide-slate-800/40 rounded-lg border border-slate-800/60 bg-slate-900/30">
            {/* GitHub (from live check) */}
            <div className="flex items-center gap-3 px-4 py-2.5">
              {githubConnected ? (
                <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-500" aria-hidden="true" />
              ) : (
                <CircleDot className="h-4 w-4 shrink-0 text-slate-600" aria-hidden="true" />
              )}
              <GitBranch className="h-3.5 w-3.5 shrink-0 text-slate-500" aria-hidden="true" />
              <span className="flex-1 text-sm text-slate-300">GitHub</span>
              {githubConnected && github?.github_username && (
                <span className="text-xs text-slate-500">@{github.github_username}</span>
              )}
              <ConnectionBadge status={githubConnected ? "connected" : "not_configured"} />
            </div>

            {/* Other connections from system API */}
            {system.connections
              .filter((c) => c.name !== "GitHub")
              .map((conn) => (
                <div key={conn.name} className="flex items-center gap-3 px-4 py-2.5">
                  {conn.status === "connected" ? (
                    <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-500" aria-hidden="true" />
                  ) : conn.status === "configured" ? (
                    <CircleDot className="h-4 w-4 shrink-0 text-brand-400" aria-hidden="true" />
                  ) : (
                    <XCircle className="h-4 w-4 shrink-0 text-slate-700" aria-hidden="true" />
                  )}
                  <ConnectionIcon name={conn.name} />
                  <span className="flex-1 text-sm text-slate-300">{conn.name}</span>
                  {conn.detail && (
                    <span className="max-w-[160px] truncate text-xs text-slate-600">
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
          <h3 className="text-xs font-semibold tracking-wide text-slate-500 uppercase">
            Knowledge Base
          </h3>
          <div className="rounded-lg border border-slate-800/60 bg-slate-900/30 px-4 py-3">
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
            </div>
          </div>
        </section>

        {/* Right: Platform Info */}
        <section className="flex flex-col gap-2">
          <h3 className="text-xs font-semibold tracking-wide text-slate-500 uppercase">
            Platform
          </h3>
          <div className="rounded-lg border border-slate-800/60 bg-slate-900/30 px-4 py-3">
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
    ok: "bg-emerald-400",
    warn: "bg-amber-400",
    error: "bg-rose-400",
    neutral: "bg-slate-600",
  }[status];

  return (
    <div className="flex items-center gap-3 rounded-xl border border-slate-800/60 bg-slate-900/40 px-4 py-3">
      <Icon className="h-4 w-4 shrink-0 text-slate-500" aria-hidden="true" />
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium text-slate-200">{value}</p>
        <p className="text-xs text-slate-600">{label}</p>
      </div>
      <span className={`h-2 w-2 shrink-0 rounded-full ${dotColor}`} />
    </div>
  );
}

function ConnectionBadge({ status }: { status: string }) {
  if (status === "connected") {
    return (
      <span className="rounded bg-emerald-500/10 px-1.5 py-0.5 text-[10px] font-medium text-emerald-400 ring-1 ring-inset ring-emerald-500/20">
        connected
      </span>
    );
  }
  if (status === "configured") {
    return (
      <span className="rounded bg-brand-500/10 px-1.5 py-0.5 text-[10px] font-medium text-brand-300 ring-1 ring-inset ring-brand-500/20">
        configured
      </span>
    );
  }
  return (
    <span className="rounded bg-slate-800 px-1.5 py-0.5 text-[10px] font-medium text-slate-600 ring-1 ring-inset ring-slate-700">
      not configured
    </span>
  );
}

function ConnectionIcon({ name }: { name: string }) {
  const className = "h-3.5 w-3.5 shrink-0 text-slate-500";
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
      <span className="text-sm text-slate-500">{label}</span>
      <span
        className={`text-sm font-medium tabular-nums ${highlight ? "text-brand-300" : "text-slate-300"}`}
      >
        {value}
      </span>
    </div>
  );
}
