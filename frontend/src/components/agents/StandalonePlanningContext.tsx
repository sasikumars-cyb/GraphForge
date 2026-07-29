/**
 * Shared UI for the standalone Development/Testing execution flow (AI
 * Workspace, not a Workflow): explains that the run has no prior-stage
 * context by default, and lets the user optionally ground it in a
 * specific completed Planning run — see CreateRunRequest.planning_run_id.
 *
 * Backend behavior when nothing is selected is byte-for-byte unchanged
 * (the field is omitted from the request entirely, see the pages that
 * use this).
 */
import { useEffect, useState } from "react";
import { Info, Lightbulb } from "lucide-react";
import { useAuth } from "../../app/auth-context";
import { listAgentRuns } from "../../lib/api/agentRuns";
import type { RunListItem } from "../../types/agent";

export function useRecentPlanningRuns() {
  const { token } = useAuth();
  const [runs, setRuns] = useState<RunListItem[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!token) return;
    const controller = new AbortController();
    listAgentRuns(
      token,
      { goal: "plan_freeform", status: "completed", page_size: 10 },
      controller.signal,
    )
      .then((res) => setRuns(res.items))
      .catch(() => setRuns([])) // best-effort — an empty picker just means "run standalone"
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, [token]);

  return { runs, loading };
}

interface PlanningRunPickerProps {
  value: string | null;
  onChange: (runId: string | null) => void;
  disabled?: boolean;
}

/** Optional selector: "None (run standalone)" or a recent completed
 * Planning run, identified by its title/subject and completion time. */
export function PlanningRunPicker({ value, onChange, disabled }: PlanningRunPickerProps) {
  const { runs, loading } = useRecentPlanningRuns();

  return (
    <div>
      <label htmlFor="planning-run-picker" className="block text-xs font-medium text-slate-400">
        Ground in a previous Planning run <span className="text-slate-600">(optional)</span>
      </label>
      <select
        id="planning-run-picker"
        value={value ?? ""}
        disabled={disabled || loading}
        onChange={(e) => onChange(e.target.value || null)}
        className="mt-1.5 w-full rounded-lg border border-slate-700 bg-slate-800/60 px-3 py-2 text-sm text-slate-200 focus:border-sky-500 focus:outline-none focus:ring-1 focus:ring-sky-500 disabled:opacity-50"
      >
        <option value="">None — run standalone</option>
        {runs.map((r) => (
          <option key={r.run_id} value={r.run_id}>
            {r.title || r.subject.display_name} ·{" "}
            {r.completed_at ? new Date(r.completed_at).toLocaleDateString() : ""}
          </option>
        ))}
      </select>
      {!loading && runs.length === 0 && (
        <p className="mt-1 text-xs text-slate-600">
          No completed Planning runs yet — run one from{" "}
          <span className="text-sky-500">AI Workspace → Planning</span> first if you'd like to
          ground this run in one.
        </p>
      )}
    </div>
  );
}

/** Explains, before submission, that this run has no Workflow behind it —
 * so Development/Testing won't see a prior stage's result unless one is
 * explicitly selected above. */
export function StandaloneContextBanner({ planningRunId }: { planningRunId: string | null }) {
  if (planningRunId) {
    return (
      <div className="flex items-start gap-2.5 rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-4 py-3 text-sm text-emerald-200">
        <Lightbulb className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
        <p>
          This run will be grounded in the selected Planning run's result — the same context a
          Workflow stage would see.
        </p>
      </div>
    );
  }

  return (
    <div className="flex items-start gap-2.5 rounded-lg border border-sky-500/30 bg-sky-500/10 px-4 py-3 text-sm text-sky-200">
      <Info className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
      <p>
        Running standalone — this request has no Workflow behind it, so it won't see a prior
        Planning result unless you select one above. Inside a Workflow, this same agent
        automatically reads the Planning stage's full result.
      </p>
    </div>
  );
}
