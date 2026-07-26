import { useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { ArrowLeft, GitMerge } from "lucide-react";
import { StatusBadge } from "../StatusBadge";
import type { AgentStep, WorkflowDetail } from "../../types/agent";
import {
  computeElapsedMs,
  estimateRemainingMs,
  formatDuration,
  progressFraction,
  stageLabel,
  workflowStatusDisplay,
  workflowTypeLabel,
  type WorkflowPhase,
} from "../../lib/workflowDerived";

interface WorkflowHeaderProps {
  workflow: WorkflowDetail;
  completedSteps: AgentStep[];
  /** Derived once in WorkflowPage via deriveWorkflowState() — the header
   * never re-derives its own opinion of workflow status from raw fields,
   * so it can't disagree with the pipeline or the approval/failure banner. */
  phase: WorkflowPhase;
}

/** Feature 1 — Workflow Command Center header: title, status, progress,
 * live-ticking duration, current stage, and a rough remaining-time
 * estimate. Every number here is computed from real workflow/run data —
 * nothing is a placeholder. */
export function WorkflowHeader({ workflow, completedSteps, phase }: WorkflowHeaderProps) {
  // Every state nothing further will run in automatically — not just
  // workflow.status === "completed" (legacy_sdlc/auto_execution). A
  // Planning blueprint's own terminal decision (approved/rejected) and a
  // failed stage (workflow.status stays "in_progress", only the current
  // stage's own status flips) are equally "done": duration must stop
  // ticking and "Est. remaining" must stop claiming to still be
  // calculating once nothing is actually in flight.
  const isDone =
    workflow.status === "completed" ||
    workflow.status === "approved" ||
    workflow.status === "rejected" ||
    phase === "failed";
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (isDone) return;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [isDone]);

  const elapsedMs = computeElapsedMs(workflow.created_at, workflow.updated_at, isDone, now);
  const fraction = progressFraction(workflow.stages);
  const completedCount = workflow.stages.filter((s) => s.status === "completed").length;
  const remainingCount = workflow.stages.length - completedCount;
  const remainingMs = isDone ? null : estimateRemainingMs(completedSteps, remainingCount);
  // Checked ahead of the generic isDone branch — isDone is already true
  // whenever phase is "failed", so "Complete" would otherwise shadow the
  // more specific "(failed)" label a failed workflow needs to show.
  const currentLabel =
    phase === "failed"
      ? `${stageLabel(workflow.current_stage)} (failed)`
      : isDone
        ? "Complete"
        : stageLabel(workflow.current_stage);
  const status = workflowStatusDisplay(workflow, phase);

  return (
    <div className="flex flex-col gap-5 rounded-2xl border border-slate-800 bg-gradient-to-br from-slate-900 to-slate-900/40 p-6 shadow-sm shadow-black/20">
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-start gap-3">
          <div className="rounded-xl bg-brand-500/10 p-2.5 ring-1 ring-inset ring-brand-500/30">
            <GitMerge className="h-5 w-5 text-brand-400" aria-hidden="true" />
          </div>
          <div>
            <p className="text-xs font-semibold uppercase tracking-wide text-brand-400">
              {workflowTypeLabel(workflow.workflow_type)}
            </p>
            <h1 className="font-display text-xl font-bold tracking-tight text-slate-50 sm:text-2xl">
              {workflow.title}
            </h1>
            {workflow.original_prompt && workflow.original_prompt !== workflow.title && (
              <details className="mt-1 max-w-2xl">
                <summary className="cursor-pointer text-xs text-slate-500 hover:text-slate-300">
                  {workflow.title} is AI-generated — show what I actually submitted
                </summary>
                <p className="mt-1.5 whitespace-pre-wrap rounded-lg border border-slate-800 bg-slate-950/60 p-3 text-sm text-slate-300">
                  {workflow.original_prompt}
                </p>
              </details>
            )}
          </div>
        </div>
        <Link
          to="/runs"
          className="flex shrink-0 items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium text-slate-400 ring-1 ring-inset ring-slate-700 transition-colors hover:bg-slate-800 hover:text-slate-200"
        >
          <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" />
          All Runs
        </Link>
      </div>

      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <Metric label="Status">
          <StatusBadge label={status.label} tone={status.tone} />
        </Metric>
        <Metric label="Current stage" value={currentLabel} />
        <Metric label="Duration" value={formatDuration(elapsedMs)} mono />
        <Metric
          label="Est. remaining"
          value={
            remainingMs === null
              ? isDone
                ? "—"
                : "calculating…"
              : `~${formatDuration(remainingMs)}`
          }
          mono
        />
      </div>

      <div>
        <div className="mb-1.5 flex items-center justify-between text-xs">
          <span className="font-medium text-slate-300">
            Stage {Math.min(completedCount + (isDone ? 0 : 1), workflow.stages.length)} of{" "}
            {workflow.stages.length}
          </span>
          <span className="text-slate-500">{Math.round(fraction * 100)}% complete</span>
        </div>
        <div className="h-2 overflow-hidden rounded-full bg-slate-800">
          <div
            className="h-full rounded-full bg-gradient-to-r from-brand-500 to-brand-glow transition-[width] duration-700 ease-out"
            style={{ width: `${Math.max(fraction * 100, isDone ? 100 : 3)}%` }}
          />
        </div>
      </div>
    </div>
  );
}

function Metric({
  label,
  value,
  mono,
  children,
}: {
  label: string;
  value?: string;
  mono?: boolean;
  children?: ReactNode;
}) {
  return (
    <div>
      <p className="text-[11px] font-medium uppercase tracking-wide text-slate-500">{label}</p>
      <div
        className={`mt-1 text-sm font-semibold text-slate-100 ${mono ? "font-mono tabular-nums" : ""}`}
      >
        {children ?? value}
      </div>
    </div>
  );
}
