import { CheckCircle2, CircleDot, XCircle } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { useAuth } from "../../app/auth-context";
import { getSystemStatus } from "../../lib/api/system";
import { getConnectionStatus } from "../../lib/api/github";

/**
 * Deliberately compact — a full AI-Providers/Connections/Platform-Info
 * breakdown already lives here in the pre-Mission-Control Control Center,
 * and Mission Control isn't meant to feel like a settings page. Anything
 * genuinely unhealthy is elevated into NeedsAttentionPanel already; this
 * strip exists only to answer "is everything basically fine" at a glance,
 * once per session, not to duplicate Settings' own detail.
 */
export function SystemHealthSummary() {
  const { token } = useAuth();
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

  if (systemQuery.isPending || githubQuery.isPending) {
    return (
      <section aria-label="System health" className="flex flex-col gap-2">
        <h2 className="text-sm font-semibold text-fg">System health</h2>
        <div className="h-32 animate-pulse rounded-xl border border-line-muted bg-surface-raised" />
      </section>
    );
  }

  const system = systemQuery.data;
  if (!system) {
    return (
      <section aria-label="System health" className="flex flex-col gap-2">
        <h2 className="text-sm font-semibold text-fg">System health</h2>
        <p className="rounded-xl border border-line-muted bg-surface px-4 py-3 text-xs text-fg-muted">
          Unavailable right now.
        </p>
      </section>
    );
  }

  const githubConnected = githubQuery.data?.connected ?? false;
  const activeProvider = system.ai_providers.find((p) => p.active);
  const healthLabel =
    system.platform_status === "healthy"
      ? "Healthy"
      : system.platform_status === "degraded"
        ? "Degraded"
        : "Error";
  const healthColor =
    system.platform_status === "healthy" ? "text-success-fg" : "text-danger-fg";

  // The few connections worth a glance at this size: GitHub (from the live
  // check, since that's the one this UI can independently verify) plus
  // whichever of the system API's own connections are actually configured
  // — an all-"not configured" row would just be noise here.
  const notableConnections = system.connections.filter(
    (c) => c.name !== "GitHub" && c.status !== "not_configured",
  );

  return (
    <section aria-label="System health" className="flex flex-col gap-2">
      <h2 className="text-sm font-semibold text-fg">
        System health <span className={`font-normal ${healthColor}`}>· {healthLabel}</span>
      </h2>
      <div className="divide-y divide-line-muted rounded-xl border border-line-muted bg-surface">
        <HealthRow
          label="GitHub"
          value={githubConnected ? "Connected" : "Not connected"}
          ok={githubConnected}
        />
        {notableConnections.map((conn) => (
          <HealthRow
            key={conn.name}
            label={conn.name}
            value={conn.status === "connected" ? "Connected" : "Configured"}
            ok={conn.status === "connected"}
          />
        ))}
        {activeProvider && (
          <HealthRow
            label={activeProvider.name}
            value={activeProvider.configured ? "Active" : "Not configured"}
            ok={activeProvider.configured}
          />
        )}
      </div>
    </section>
  );
}

function HealthRow({ label, value, ok }: { label: string; value: string; ok: boolean }) {
  const Icon = ok ? CheckCircle2 : value === "Not connected" ? XCircle : CircleDot;
  return (
    <div className="flex items-center gap-3 px-4 py-2">
      <Icon
        className={`h-3.5 w-3.5 shrink-0 ${ok ? "text-success-fg" : "text-fg-subtle"}`}
        aria-hidden="true"
      />
      <span className="flex-1 truncate text-sm text-fg-secondary capitalize">{label}</span>
      <span className="shrink-0 text-xs text-fg-muted">{value}</span>
    </div>
  );
}
