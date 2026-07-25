import { Link } from "react-router-dom";
import { useEffect, useState } from "react";
import { StatusBadge } from "../components/StatusBadge";
import { StatCard } from "../components/dashboard/StatCard";
import { useAuth } from "../app/auth-context";
import { listWorkflows } from "../lib/api/workflows";
import { listAgentRuns } from "../lib/api/agentRuns";
import {
  deriveWorkflowState,
  workflowStatusDisplay,
  stageLabel,
  progressFraction,
} from "../lib/workflowDerived";
import type { WorkflowListItem } from "../types/agent";
import type { RunListItem } from "../types/agent";
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  CircleDot,
  Flame,
  GitMerge,
  Loader2,
  Plus,
  Sparkles,
  TrendingUp,
  XCircle,
} from "lucide-react";

export function DashboardPage() {
  const { token } = useAuth();
  const [workflows, setWorkflows] = useState<WorkflowListItem[]>([]);
  const [workflowsError, setWorkflowsError] = useState<string | null>(null);
  const [approvedTotal, setApprovedTotal] = useState<number | null>(null);
  const [recentRuns, setRecentRuns] = useState<RunListItem[]>([]);

  useEffect(() => {
    if (!token) return;
    listWorkflows(token, { page_size: 10 })
      .then((res) => setWorkflows(res.items))
      .catch((err) => {
        setWorkflowsError(err instanceof Error ? err.message : "Failed to load workflows.");
      });
  }, [token]);

  useEffect(() => {
    if (!token) return;
    listWorkflows(token, { status: "approved", page_size: 1 })
      .then((res) => setApprovedTotal(res.total))
      .catch(() => setApprovedTotal(null));
  }, [token]);

  useEffect(() => {
    if (!token) return;
    listAgentRuns(token, { page_size: 8 })
      .then((res) => setRecentRuns(res.items))
      .catch(() => setRecentRuns([]));
  }, [token]);

  // ── Derived state ──────────────────────────────────────────────
  const planningCount = workflows.filter((w) => w.workflow_type === "planning").length;
  const failedWorkflows = workflows.filter((w) => {
    const { phase } = deriveWorkflowState(w);
    return phase === "failed";
  });
  const activeWorkflows = workflows.filter((w) => {
    const { phase } = deriveWorkflowState(w);
    return phase === "running" || phase === "waiting";
  });
  const completedWorkflows = workflows.filter((w) => {
    const { phase } = deriveWorkflowState(w);
    return phase === "completed" || phase === "approved";
  });

  const totalRuns = recentRuns.length;
  const failedRuns = recentRuns.filter((r) => r.status === "failed").length;
  const completedRuns = recentRuns.filter((r) => r.status === "completed").length;
  const successRate = totalRuns > 0 ? Math.round((completedRuns / totalRuns) * 100) : 0;

  const needsAttentionCount =
    failedWorkflows.length + (approvedTotal !== null && approvedTotal > 0 ? 1 : 0);

  return (
    <div className="flex flex-col gap-6">
      {/* ── Header ──────────────────────────────────────────────── */}
      <div>
        <h2 className="text-2xl font-bold tracking-tight text-slate-50">
          Welcome back to <span className="text-brand-400">GraphForge</span>
        </h2>
        <p className="mt-1 text-sm text-slate-400">
          {needsAttentionCount > 0
            ? `${needsAttentionCount} item${needsAttentionCount > 1 ? "s" : ""} need${needsAttentionCount === 1 ? "s" : ""} attention`
            : planningCount > 0
              ? `${planningCount} Planning${approvedTotal !== null && approvedTotal > 0 ? ` · ${approvedTotal} Approved` : ""}`
              : "AI-powered software engineering at your fingertips."}
        </p>
      </div>

      {/* ── KPI Strip ───────────────────────────────────────────── */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        <StatCard label="Recent Runs" value={totalRuns} icon={Activity} color="brand" />
        <StatCard
          label="Active Workflows"
          value={activeWorkflows.length}
          icon={Loader2}
          color="brand"
        />
        <StatCard label="Success Rate" value={`${successRate}%`} icon={TrendingUp} color="emerald" />
        <StatCard label="Failed Runs" value={failedRuns} icon={Flame} color={failedRuns > 0 ? "rose" : "slate"} />
        <StatCard
          label="Approval Queue"
          value={approvedTotal ?? 0}
          icon={CheckCircle2}
          color={approvedTotal && approvedTotal > 0 ? "amber" : "slate"}
        />
      </div>

      {/* ── Error state ─────────────────────────────────────────── */}
      {workflowsError && (
        <div className="rounded-lg border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-300">
          {workflowsError}
        </div>
      )}

      {/* ── Two-column grid: Attention + Active Workflows ───────── */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {/* Left: Needs Attention */}
        <div className="flex flex-col gap-2">
          <h3 className="text-xs font-semibold tracking-wide text-slate-500 uppercase">
            Needs Attention
          </h3>
          {needsAttentionCount === 0 ? (
            <div className="flex items-center gap-2 rounded-lg border border-slate-800/40 bg-slate-900/30 px-4 py-6 text-center">
              <CheckCircle2 className="mx-auto h-4 w-4 text-emerald-600" aria-hidden="true" />
              <span className="text-xs text-slate-600">All clear — nothing requires action.</span>
            </div>
          ) : (
            <div className="space-y-1.5">
              {failedWorkflows.length > 0 && (
                <Link
                  to="/runs"
                  className="flex items-center gap-3 rounded-lg border border-rose-500/15 bg-rose-500/5 px-4 py-2.5 transition-colors hover:bg-rose-500/10"
                >
                  <XCircle className="h-4 w-4 shrink-0 text-rose-400" aria-hidden="true" />
                  <span className="flex-1 text-sm text-slate-200">
                    {failedWorkflows.length} failed workflow{failedWorkflows.length > 1 ? "s" : ""}
                  </span>
                  <span className="text-xs text-slate-600">View →</span>
                </Link>
              )}
              {approvedTotal !== null && approvedTotal > 0 && (
                <Link
                  to="/workflows/approved"
                  className="flex items-center gap-3 rounded-lg border border-amber-500/15 bg-amber-500/5 px-4 py-2.5 transition-colors hover:bg-amber-500/10"
                >
                  <AlertTriangle className="h-4 w-4 shrink-0 text-amber-400" aria-hidden="true" />
                  <span className="flex-1 text-sm text-slate-200">
                    {approvedTotal} awaiting implementation
                  </span>
                  <span className="text-xs text-slate-600">
                    Approved Queue ({approvedTotal}) →
                  </span>
                </Link>
              )}
            </div>
          )}
        </div>

        {/* Right: Active Workflows */}
        <div className="flex flex-col gap-2">
          <div className="flex items-center justify-between">
            <h3 className="text-xs font-semibold tracking-wide text-slate-500 uppercase">
              Active Workflows
            </h3>
            {activeWorkflows.length > 0 && (
              <Link
                to="/runs"
                className="text-xs text-slate-600 transition-colors hover:text-slate-400"
              >
                All Runs →
              </Link>
            )}
          </div>
          {activeWorkflows.length === 0 ? (
            <div className="flex items-center gap-2 rounded-lg border border-slate-800/40 bg-slate-900/30 px-4 py-6 text-center">
              <GitMerge className="mx-auto h-4 w-4 text-slate-700" aria-hidden="true" />
              <span className="text-xs text-slate-600">No workflows running.</span>
            </div>
          ) : (
            <div className="divide-y divide-slate-800/40 rounded-lg border border-slate-800/60 bg-slate-900/30">
              {activeWorkflows.slice(0, 4).map((w) => {
                const { phase } = deriveWorkflowState(w);
                const status = workflowStatusDisplay(w, phase);
                const progress = progressFraction(w.stages);
                const pct = Math.round(progress * 100);
                return (
                  <Link
                    key={w.workflow_id}
                    to={`/workflows/${w.workflow_id}`}
                    className="flex items-center gap-3 px-4 py-2.5 transition-colors hover:bg-slate-800/30"
                  >
                    <Loader2
                      className="h-3 w-3 shrink-0 animate-spin text-brand-400"
                      aria-hidden="true"
                    />
                    <span className="min-w-0 flex-1 truncate text-sm text-slate-300">
                      {w.title}
                    </span>
                    <span className="shrink-0 text-xs text-slate-600">
                      {stageLabel(w.current_stage)}
                    </span>
                    <StatusBadge label={status.label} tone={status.tone} />
                    <span className="w-8 shrink-0 text-right text-xs tabular-nums text-slate-600">
                      {pct}%
                    </span>
                  </Link>
                );
              })}
            </div>
          )}
        </div>
      </div>

      {/* ── Two-column grid: Activity Feed + Quick Start ────────── */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-5">
        {/* Left (3/5): Activity Feed */}
        <div className="flex flex-col gap-2 lg:col-span-3">
          <div className="flex items-center justify-between">
            <h3 className="text-xs font-semibold tracking-wide text-slate-500 uppercase">
              Recent Activity
            </h3>
            <Link
              to="/runs"
              className="text-xs text-slate-600 transition-colors hover:text-slate-400"
            >
              All Runs →
            </Link>
          </div>
          {recentRuns.length === 0 && completedWorkflows.length === 0 ? (
            <div className="flex items-center justify-center rounded-lg border border-slate-800/40 bg-slate-900/30 px-4 py-8">
              <span className="text-xs text-slate-600">No activity yet.</span>
            </div>
          ) : (
            <div className="divide-y divide-slate-800/40 rounded-lg border border-slate-800/60 bg-slate-900/30">
              {recentRuns.slice(0, 6).map((run) => (
                <Link
                  key={run.run_id}
                  to={`/runs/${run.run_id}`}
                  className="flex items-center gap-3 px-4 py-2 transition-colors hover:bg-slate-800/30"
                >
                  {run.status === "completed" && (
                    <CheckCircle2
                      className="h-3.5 w-3.5 shrink-0 text-emerald-500"
                      aria-hidden="true"
                    />
                  )}
                  {run.status === "failed" && (
                    <XCircle className="h-3.5 w-3.5 shrink-0 text-rose-500" aria-hidden="true" />
                  )}
                  {run.status === "running" && (
                    <Loader2
                      className="h-3.5 w-3.5 shrink-0 animate-spin text-brand-400"
                      aria-hidden="true"
                    />
                  )}
                  {(run.status === "queued" || run.status === "partial") && (
                    <CircleDot
                      className="h-3.5 w-3.5 shrink-0 text-slate-500"
                      aria-hidden="true"
                    />
                  )}
                  <span className="min-w-0 flex-1 truncate text-sm text-slate-300">
                    {run.title ?? run.goal}
                  </span>
                  <StatusBadge
                    label={run.status === "completed" ? "Done" : run.status === "failed" ? "Failed" : run.status === "running" ? "Running" : run.status}
                    tone={run.status === "completed" ? "success" : run.status === "failed" ? "danger" : run.status === "running" ? "info" : "neutral"}
                  />
                  {run.started_at && (
                    <span className="shrink-0 text-xs text-slate-600">
                      {formatRelativeTime(run.started_at)}
                    </span>
                  )}
                </Link>
              ))}
            </div>
          )}
        </div>

        {/* Right (2/5): Quick Start */}
        <div className="flex flex-col gap-2 lg:col-span-2">
          <h3 className="text-xs font-semibold tracking-wide text-slate-500 uppercase">
            Quick Start
          </h3>
          <div className="flex flex-col gap-1.5">
            <Link
              to="/workflows/new"
              className="flex items-center gap-3 rounded-lg border border-slate-800/60 bg-slate-900/30 px-4 py-3 transition-colors hover:border-brand-500/30 hover:bg-slate-900/60"
            >
              <Plus className="h-4 w-4 text-brand-400" aria-hidden="true" />
              <div>
                <p className="text-sm font-medium text-slate-200">Start Workflow</p>
                <p className="text-xs text-slate-600">Plan → Build → Test → Review</p>
              </div>
            </Link>
            <Link
              to="/workspace"
              className="flex items-center gap-3 rounded-lg border border-slate-800/60 bg-slate-900/30 px-4 py-3 transition-colors hover:border-brand-500/30 hover:bg-slate-900/60"
            >
              <Sparkles className="h-4 w-4 text-brand-400" aria-hidden="true" />
              <div>
                <p className="text-sm font-medium text-slate-200">Run AI Capability</p>
                <p className="text-xs text-slate-600">Planning, testing, review & more</p>
              </div>
            </Link>
            <Link
              to="/repositories"
              className="flex items-center gap-3 rounded-lg border border-slate-800/60 bg-slate-900/30 px-4 py-3 transition-colors hover:border-brand-500/30 hover:bg-slate-900/60"
            >
              <Activity className="h-4 w-4 text-brand-400" aria-hidden="true" />
              <div>
                <p className="text-sm font-medium text-slate-200">Manage Repositories</p>
                <p className="text-xs text-slate-600">Connect, index & explore</p>
              </div>
            </Link>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Helpers ────────────────────────────────────────────────────────

function formatRelativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60_000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  return `${days}d ago`;
}
