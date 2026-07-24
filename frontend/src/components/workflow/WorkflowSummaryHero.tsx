import { CheckCircle2 } from "lucide-react";
import type { AgentStep, WorkflowDetail } from "../../types/agent";
import { computeElapsedMs, formatDuration, resultRepositories } from "../../lib/workflowDerived";

interface WorkflowSummaryHeroProps {
  workflow: WorkflowDetail;
  steps: AgentStep[];
  /** "completed" (legacy_sdlc/auto_execution terminal state) vs "approved"
   * (a Planning blueprint the human approved). Same stat layout for both —
   * only the heading changes, so a Planning approval never claims a
   * "complete" SDLC run that never wrote any code. */
  variant?: "completed" | "approved";
}

/** Feature 8 — the executive summary shown once a workflow reaches a
 * positive terminal state. Every number here is a real aggregate over the
 * workflow's own runs: duration from real timestamps, evidence counted
 * directly, repositories deduped from repositories_consulted, confidence
 * averaged across steps that reported one. Nothing is a fixed/sample
 * value. */
export function WorkflowSummaryHero({
  workflow,
  steps,
  variant = "completed",
}: WorkflowSummaryHeroProps) {
  const elapsedMs = computeElapsedMs(workflow.created_at, workflow.updated_at, true, Date.now());
  const evidenceCount = steps.reduce((sum, s) => sum + s.evidence.length, 0);
  const repoSet = new Set<string>();
  steps.forEach((s) => resultRepositories(s.result).forEach((r) => repoSet.add(r)));
  const scored = steps.filter((s) => s.confidence.score !== null);
  const avgConfidence =
    scored.length > 0
      ? scored.reduce((sum, s) => sum + (s.confidence.score as number), 0) / scored.length
      : null;

  return (
    <div className="flex flex-col gap-6 rounded-2xl border border-emerald-500/25 bg-gradient-to-br from-emerald-500/10 via-slate-900 to-slate-900 p-7 text-center">
      <div className="flex flex-col items-center gap-2">
        <div className="flex h-12 w-12 items-center justify-center rounded-full bg-emerald-500/15 ring-1 ring-inset ring-emerald-500/40">
          <CheckCircle2 className="h-6 w-6 text-emerald-400" aria-hidden="true" />
        </div>
        <p className="text-xs font-semibold uppercase tracking-wide text-emerald-400">
          {variant === "approved" ? "Blueprint Approved" : "Workflow Complete"}
        </p>
        <h2 className="font-display text-2xl font-bold tracking-tight text-slate-50">
          {workflow.title}
        </h2>
      </div>

      <div className="mx-auto grid w-full max-w-2xl grid-cols-2 gap-5 sm:grid-cols-5">
        <SummaryStat label="Duration" value={formatDuration(elapsedMs)} />
        <SummaryStat
          label="Stages"
          value={String(workflow.stages.filter((s) => s.status === "completed").length)}
        />
        <SummaryStat label="Evidence" value={`${evidenceCount} facts`} />
        <SummaryStat label="Repositories" value={String(repoSet.size)} />
        <SummaryStat
          label="Confidence"
          value={avgConfidence !== null ? `${Math.round(avgConfidence * 100)}%` : "—"}
        />
      </div>
    </div>
  );
}

function SummaryStat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="font-mono text-lg font-bold tabular-nums text-slate-50 sm:text-xl">{value}</p>
      <p className="mt-0.5 text-[11px] font-medium uppercase tracking-wide text-slate-500">
        {label}
      </p>
    </div>
  );
}
