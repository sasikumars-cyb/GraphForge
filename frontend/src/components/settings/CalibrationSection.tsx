import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import { Card } from "../Card";
import { useAuth } from "../../app/auth-context";
import { getCalibrationSummary } from "../../lib/api/calibration";
import type { AgentCalibration } from "../../types/calibration";

/** Does a high confidence_score actually predict human approval? See
 * app.models.confidence_calibration's docstring — ROADMAP.md treats an
 * unchecked confidence score as decorative, a blocker past Phase 2. Every
 * approve/reject decision records one row per completed stage's
 * confidence_score; this view is the calibration curve that data produces:
 * a well-calibrated agent's high-confidence bucket should approve
 * materially more often than its low-confidence bucket. */
export function CalibrationSection() {
  const { token } = useAuth();
  const [agents, setAgents] = useState<AgentCalibration[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token) return;
    getCalibrationSummary(token)
      .then((data) => setAgents(data.agents))
      .catch(() => setError("Failed to load calibration data."));
  }, [token]);

  if (error) {
    return (
      <div className="rounded-lg border border-rose-500/30 bg-rose-500/5 px-4 py-3 text-sm text-rose-300">
        {error}
      </div>
    );
  }

  if (agents === null) {
    return (
      <div className="flex items-center gap-2 py-8 text-sm text-slate-500">
        <Loader2 className="h-4 w-4 animate-spin" />
        Loading calibration data...
      </div>
    );
  }

  if (agents.length === 0) {
    return (
      <div className="rounded-lg border border-slate-800/60 bg-slate-900/30 px-4 py-6 text-center text-sm text-slate-500">
        No approve/reject decisions recorded yet — this fills in as workflows get approved or
        rejected.
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <p className="text-xs text-slate-500">
        Checks each agent's confidence_score against whether a human went on to approve or reject
        the workflow it was part of. A well-calibrated agent should approve materially more often
        in its high-confidence bucket than its low-confidence one — if the rates are close, the
        score isn't carrying real signal yet.
      </p>
      {agents.map((agent) => (
        <Card key={agent.agent_id} title={agent.agent_id}>
          <div className="mb-3 grid grid-cols-3 gap-4">
            <div>
              <p className="text-xs text-slate-500">Decisions</p>
              <p className="mt-0.5 text-lg font-semibold text-slate-100">
                {agent.total_decisions}
              </p>
            </div>
            <div>
              <p className="text-xs text-slate-500">Overall approval rate</p>
              <p className="mt-0.5 text-lg font-semibold text-emerald-400">
                {(agent.approval_rate * 100).toFixed(0)}%
              </p>
            </div>
            <div>
              <p className="text-xs text-slate-500">Avg. confidence</p>
              <p className="mt-0.5 text-lg font-semibold text-sky-400">
                {(agent.avg_confidence * 100).toFixed(0)}%
              </p>
            </div>
          </div>
          <div className="divide-y divide-slate-800/60 rounded-lg border border-slate-800/60">
            {agent.buckets.map((b) => (
              <div key={b.bucket} className="flex items-center justify-between px-3 py-2 text-xs">
                <span className="font-mono text-slate-400">{b.bucket}</span>
                <span className="text-slate-500">{b.total} decision{b.total === 1 ? "" : "s"}</span>
                <span
                  className={`font-semibold ${b.approval_rate >= 0.5 ? "text-emerald-400" : "text-amber-400"}`}
                >
                  {(b.approval_rate * 100).toFixed(0)}% approved
                </span>
              </div>
            ))}
          </div>
        </Card>
      ))}
    </div>
  );
}
