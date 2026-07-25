import { useCallback, useEffect, useState } from "react";
import { useAuth } from "../app/auth-context";
import { checkToolHealth, configureTool, listTools } from "../lib/api/tools";
import type { Tool, ToolHealth } from "../types/tools";
import { Card } from "./Card";

const HEALTH_STYLES: Record<ToolHealth, { dot: string; label: string }> = {
  healthy: { dot: "bg-emerald-400", label: "Healthy" },
  unconfigured: { dot: "bg-slate-500", label: "Unconfigured" },
  offline: { dot: "bg-red-400", label: "Offline" },
  auth_failed: { dot: "bg-amber-400", label: "Auth failed" },
  permission_denied: { dot: "bg-amber-400", label: "Permission denied" },
  rate_limited: { dot: "bg-yellow-400", label: "Rate limited" },
  unavailable: { dot: "bg-red-400", label: "Unavailable" },
};

function HealthBadge({ health }: { health: ToolHealth }) {
  const s = HEALTH_STYLES[health] ?? HEALTH_STYLES.unavailable;
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-slate-700 bg-slate-800 px-2.5 py-0.5 text-xs font-medium text-slate-300">
      <span className={`h-1.5 w-1.5 rounded-full ${s.dot}`} />
      {s.label}
    </span>
  );
}

function ToolRow({ tool, onToggle, onHealthCheck }: {
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
    <div className="flex items-start justify-between gap-4 py-4">
      <div className="flex items-start gap-3">
        <span className="mt-0.5 text-xl" aria-hidden="true">{tool.icon}</span>
        <div>
          <p className="text-sm font-medium text-slate-100">{tool.display_name}</p>
          <p className="mt-0.5 text-xs text-slate-400">{tool.description}</p>
          {tool.notes && (
            <p className="mt-1 text-xs text-slate-500">{tool.notes}</p>
          )}
          <div className="mt-2 flex flex-wrap gap-1.5">
            {tool.capabilities.map((cap) => (
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

      <div className="flex shrink-0 items-center gap-3">
        <HealthBadge health={tool.health} />

        <button
          onClick={handleHealthCheck}
          disabled={checking}
          className="text-xs text-slate-500 hover:text-sky-400 disabled:opacity-40"
          title="Run health check"
        >
          {checking ? "Checking…" : "Check"}
        </button>

        <label className="relative inline-flex h-6 w-11 shrink-0 cursor-pointer items-center">
          <input
            type="checkbox"
            checked={tool.enabled}
            onChange={handleToggle}
            disabled={toggling}
            className="peer sr-only"
          />
          <span className="h-6 w-11 rounded-full bg-slate-700 transition-colors peer-checked:bg-sky-500 peer-disabled:opacity-50" />
          <span className="absolute left-0.5 h-5 w-5 rounded-full bg-white transition-transform peer-checked:translate-x-5" />
        </label>
      </div>
    </div>
  );
}

export function ToolRegistrySection() {
  const { token } = useAuth();
  const [tools, setTools] = useState<Tool[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchTools = useCallback(async () => {
    if (!token) return;
    try {
      const data = await listTools(token);
      setTools(data);
      setError(null);
    } catch {
      setError("Failed to load tools.");
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    void fetchTools();
  }, [fetchTools]);

  const handleToggle = useCallback(async (tool: Tool, enabled: boolean) => {
    if (!token) return;
    const updated = await configureTool(token, tool.tool_id, {
      enabled,
      config: {},
    });
    setTools((prev) => prev.map((t) => (t.tool_id === updated.tool_id ? updated : t)));
  }, [token]);

  const handleHealthCheck = useCallback(async (tool: Tool) => {
    if (!token) return;
    const result = await checkToolHealth(token, tool.tool_id);
    setTools((prev) =>
      prev.map((t) => (t.tool_id === tool.tool_id ? { ...t, health: result.health } : t))
    );
  }, [token]);

  const byCategory = tools.reduce<Record<string, Tool[]>>((acc, t) => {
    (acc[t.category] ??= []).push(t);
    return acc;
  }, {});

  const categoryLabel: Record<string, string> = {
    graph: "Knowledge Graph",
    code_intelligence: "Code Intelligence",
    project_management: "Project Management",
    documentation: "Documentation",
    communication: "Communication",
    monitoring: "Monitoring",
    filesystem: "File System",
    custom: "Custom",
  };

  return (
    <Card
      title="Tool Registry"
      description="Engineering tools available to AI agents. Toggle to enable or disable. Enabled tools are discovered automatically by the Planning Agent."
    >
      {loading && (
        <p className="text-sm text-slate-500">Loading tools…</p>
      )}
      {error && (
        <p className="text-sm text-red-400">{error}</p>
      )}
      {!loading && !error && tools.length === 0 && (
        <p className="text-sm text-slate-500">No tools registered.</p>
      )}
      {!loading && !error && Object.entries(byCategory).map(([cat, catTools]) => (
        <div key={cat} className="mb-4 last:mb-0">
          <p className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-slate-500">
            {categoryLabel[cat] ?? cat}
          </p>
          <div className="divide-y divide-slate-800/60">
            {catTools.map((tool) => (
              <ToolRow
                key={tool.tool_id}
                tool={tool}
                onToggle={handleToggle}
                onHealthCheck={handleHealthCheck}
              />
            ))}
          </div>
        </div>
      ))}
    </Card>
  );
}
