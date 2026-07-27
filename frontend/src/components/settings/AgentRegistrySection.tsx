import { useCallback, useEffect, useState } from "react";
import { Loader2, ShieldOff } from "lucide-react";
import { Card } from "../Card";
import { useAuth } from "../../app/auth-context";
import { disableAgent, enableAgent, listAgentManifests } from "../../lib/api/agents";
import type { AgentManifest } from "../../types/agents";

const COST_CLASS_STYLES: Record<string, string> = {
  cheap: "text-emerald-400",
  standard: "text-sky-400",
  expensive: "text-amber-400",
};

function AgentRow({
  agent,
  onToggle,
}: {
  agent: AgentManifest;
  onToggle: (agent: AgentManifest, enabled: boolean) => Promise<void>;
}) {
  const [toggling, setToggling] = useState(false);

  async function handleToggle(e: React.ChangeEvent<HTMLInputElement>) {
    setToggling(true);
    try {
      await onToggle(agent, e.target.checked);
    } finally {
      setToggling(false);
    }
  }

  return (
    <div className="flex items-center justify-between gap-4 py-2.5">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium text-slate-200">{agent.agent_id}</span>
          <span className={`text-[10px] font-medium uppercase ${COST_CLASS_STYLES[agent.cost_class] ?? "text-slate-500"}`}>
            {agent.cost_class}
          </span>
          {!agent.enabled && (
            <span className="inline-flex items-center gap-1 rounded bg-rose-500/10 px-1.5 py-0.5 text-[10px] font-medium text-rose-300 ring-1 ring-inset ring-rose-500/20">
              <ShieldOff className="h-2.5 w-2.5" aria-hidden="true" />
              Disabled
            </span>
          )}
        </div>
        <p className="mt-0.5 text-xs text-slate-500">{agent.purpose}</p>
        <div className="mt-1.5 flex flex-wrap gap-1">
          {agent.goals.map((goal) => (
            <span
              key={goal}
              className="rounded bg-slate-800/80 px-1.5 py-0.5 text-[10px] font-mono text-slate-400"
            >
              {goal}
            </span>
          ))}
        </div>
      </div>

      <div className="flex shrink-0 items-center gap-3">
        <label className="relative inline-flex h-5 w-9 shrink-0 cursor-pointer items-center">
          <input
            type="checkbox"
            checked={agent.enabled}
            onChange={(e) => void handleToggle(e)}
            disabled={toggling}
            aria-label={`${agent.enabled ? "Disable" : "Enable"} ${agent.agent_id}`}
            className="peer sr-only"
          />
          <span className="h-5 w-9 rounded-full bg-slate-700 transition-colors peer-checked:bg-sky-500 peer-disabled:opacity-50" />
          <span className="absolute left-0.5 h-4 w-4 rounded-full bg-white transition-transform peer-checked:translate-x-4" />
        </label>
      </div>
    </div>
  );
}

/** Runtime kill switch for agents — disabling here rejects new runs
 * immediately (a stage-start returns 503 agent_disabled) without a
 * deploy; already-running runs finish normally. Mirrors ToolRegistrySection's
 * pattern, one level up (agents, not the tools they call). */
export function AgentRegistrySection() {
  const { token } = useAuth();
  const [agents, setAgents] = useState<AgentManifest[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchAgents = useCallback(async () => {
    if (!token) return;
    try {
      setAgents(await listAgentManifests(token));
      setError(null);
    } catch {
      setError("Failed to load agent registry.");
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    void fetchAgents();
  }, [fetchAgents]);

  const handleToggle = useCallback(
    async (agent: AgentManifest, enabled: boolean) => {
      if (!token) return;
      if (enabled) {
        await enableAgent(token, agent.agent_id);
      } else {
        await disableAgent(token, agent.agent_id);
      }
      setAgents((prev) =>
        prev.map((a) => (a.agent_id === agent.agent_id ? { ...a, enabled } : a)),
      );
    },
    [token],
  );

  if (loading) {
    return (
      <div className="flex items-center gap-2 py-8 text-sm text-slate-500">
        <Loader2 className="h-4 w-4 animate-spin" />
        Loading agent registry...
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

  const enabledCount = agents.filter((a) => a.enabled).length;

  return (
    <div className="flex flex-col gap-5">
      <Card>
        <div className="grid grid-cols-2 gap-4">
          <div>
            <p className="text-xs text-slate-500">Registered Agents</p>
            <p className="mt-0.5 text-lg font-semibold text-slate-100">{agents.length}</p>
          </div>
          <div>
            <p className="text-xs text-slate-500">Enabled</p>
            <p className="mt-0.5 text-lg font-semibold text-emerald-400">{enabledCount}</p>
          </div>
        </div>
      </Card>

      <Card title="Agents">
        <p className="mb-2 text-xs text-slate-500">
          Disabling an agent stops new runs for it immediately — any workflow stage using it will
          fail with a clear error until it's re-enabled. Runs already in progress are unaffected.
        </p>
        <div className="divide-y divide-slate-800/60">
          {agents.map((agent) => (
            <AgentRow key={agent.agent_id} agent={agent} onToggle={handleToggle} />
          ))}
        </div>
      </Card>
    </div>
  );
}
