import { useCallback, useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import { Card } from "../Card";
import { useAuth } from "../../app/auth-context";
import { checkToolHealth, configureTool, listTools } from "../../lib/api/tools";
import type { Tool, ToolHealth } from "../../types/tools";

// ---------------------------------------------------------------------------
// Health indicator
// ---------------------------------------------------------------------------

const HEALTH_STYLES: Record<ToolHealth, { dot: string; label: string }> = {
  healthy: { dot: "bg-emerald-400", label: "Healthy" },
  unconfigured: { dot: "bg-slate-500", label: "Unconfigured" },
  offline: { dot: "bg-red-400", label: "Offline" },
  auth_failed: { dot: "bg-amber-400", label: "Auth failed" },
  permission_denied: { dot: "bg-amber-400", label: "Permission denied" },
  rate_limited: { dot: "bg-yellow-400", label: "Rate limited" },
  unavailable: { dot: "bg-red-400", label: "Unavailable" },
};

function HealthDot({ health }: { health: ToolHealth }) {
  const s = HEALTH_STYLES[health] ?? HEALTH_STYLES.unavailable;
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-slate-400">
      <span className={`h-1.5 w-1.5 rounded-full ${s.dot}`} />
      {s.label}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Capability-oriented tool row
// ---------------------------------------------------------------------------

function ToolCapabilityRow({
  tool,
  onToggle,
  onHealthCheck,
}: {
  tool: Tool;
  onToggle: (tool: Tool, enabled: boolean) => Promise<void>;
  onHealthCheck: (tool: Tool) => Promise<void>;
}) {
  const [toggling, setToggling] = useState(false);
  const [checking, setChecking] = useState(false);

  async function handleToggle(e: React.ChangeEvent<HTMLInputElement>) {
    setToggling(true);
    try {
      await onToggle(tool, e.target.checked);
    } finally {
      setToggling(false);
    }
  }

  async function handleHealthCheck() {
    setChecking(true);
    try {
      await onHealthCheck(tool);
    } finally {
      setChecking(false);
    }
  }

  return (
    <div className="flex items-center justify-between gap-4 py-2.5">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium text-slate-200">{tool.display_name}</span>
          <HealthDot health={tool.health} />
        </div>
        <p className="mt-0.5 text-xs text-slate-500">{tool.description}</p>
        <div className="mt-1.5 flex flex-wrap gap-1">
          {tool.capabilities.map((cap) => (
            <span
              key={cap}
              className="rounded bg-slate-800/80 px-1.5 py-0.5 text-[10px] font-mono text-slate-400"
            >
              {cap}
            </span>
          ))}
        </div>
      </div>

      <div className="flex shrink-0 items-center gap-3">
        <button
          type="button"
          onClick={() => void handleHealthCheck()}
          disabled={checking}
          className="text-xs text-slate-500 hover:text-sky-400 disabled:opacity-40"
        >
          {checking ? "..." : "Test"}
        </button>

        <label className="relative inline-flex h-5 w-9 shrink-0 cursor-pointer items-center">
          <input
            type="checkbox"
            checked={tool.enabled}
            onChange={(e) => void handleToggle(e)}
            disabled={toggling}
            className="peer sr-only"
          />
          <span className="h-5 w-9 rounded-full bg-slate-700 transition-colors peer-checked:bg-sky-500 peer-disabled:opacity-50" />
          <span className="absolute left-0.5 h-4 w-4 rounded-full bg-white transition-transform peer-checked:translate-x-4" />
        </label>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Capability category labels (user-facing, capability-oriented)
// ---------------------------------------------------------------------------

const CATEGORY_LABELS: Record<string, string> = {
  graph: "Architecture & Dependency Analysis",
  code_intelligence: "Code Intelligence",
  project_management: "Issue & Project Context",
  documentation: "Documentation Search",
  communication: "Communication",
  monitoring: "Monitoring & Observability",
  filesystem: "File System & Indexing",
  custom: "Custom Capabilities",
};

// ---------------------------------------------------------------------------
// Main Section
// ---------------------------------------------------------------------------

export function ToolRegistrySection() {
  const { token } = useAuth();
  const [tools, setTools] = useState<Tool[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchTools = useCallback(async () => {
    if (!token) return;
    try {
      setTools(await listTools(token));
      setError(null);
    } catch {
      setError("Failed to load tool registry.");
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    void fetchTools();
  }, [fetchTools]);

  const handleToggle = useCallback(
    async (tool: Tool, enabled: boolean) => {
      if (!token) return;
      const updated = await configureTool(token, tool.tool_id, { enabled, config: {} });
      setTools((prev) => prev.map((t) => (t.tool_id === updated.tool_id ? updated : t)));
    },
    [token],
  );

  const handleHealthCheck = useCallback(
    async (tool: Tool) => {
      if (!token) return;
      const result = await checkToolHealth(token, tool.tool_id);
      setTools((prev) =>
        prev.map((t) => (t.tool_id === tool.tool_id ? { ...t, health: result.health } : t)),
      );
    },
    [token],
  );

  if (loading) {
    return (
      <div className="flex items-center gap-2 py-8 text-sm text-slate-500">
        <Loader2 className="h-4 w-4 animate-spin" />
        Loading tool registry...
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

  // Group by category
  const byCategory = tools.reduce<Record<string, Tool[]>>((acc, t) => {
    (acc[t.category] ??= []).push(t);
    return acc;
  }, {});

  const enabledCount = tools.filter((t) => t.enabled).length;
  const healthyCount = tools.filter((t) => t.health === "healthy").length;

  return (
    <div className="flex flex-col gap-5">
      {/* Summary */}
      <Card>
        <div className="grid grid-cols-3 gap-4">
          <div>
            <p className="text-xs text-slate-500">Total Capabilities</p>
            <p className="mt-0.5 text-lg font-semibold text-slate-100">{tools.length}</p>
          </div>
          <div>
            <p className="text-xs text-slate-500">Enabled</p>
            <p className="mt-0.5 text-lg font-semibold text-emerald-400">{enabledCount}</p>
          </div>
          <div>
            <p className="text-xs text-slate-500">Healthy</p>
            <p className="mt-0.5 text-lg font-semibold text-sky-400">{healthyCount}</p>
          </div>
        </div>
      </Card>

      {/* Grouped capabilities */}
      {Object.entries(byCategory).map(([cat, catTools]) => (
        <Card key={cat} title={CATEGORY_LABELS[cat] ?? cat}>
          <div className="divide-y divide-slate-800/60">
            {catTools.map((tool) => (
              <ToolCapabilityRow
                key={tool.tool_id}
                tool={tool}
                onToggle={handleToggle}
                onHealthCheck={handleHealthCheck}
              />
            ))}
          </div>
        </Card>
      ))}
    </div>
  );
}
