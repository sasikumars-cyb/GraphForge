import { Link } from "react-router-dom";
import { useEffect, useState } from "react";
import { Card } from "../components/Card";
import { StatusBadge } from "../components/StatusBadge";
import { WorkflowTimeline } from "../components/workflow/WorkflowTimeline";
import { useAuth } from "../app/auth-context";
import { listWorkflows } from "../lib/api/workflows";
import type { WorkflowListItem } from "../types/agent";
import { Lightbulb, Search, GitMerge } from "lucide-react";

export function DashboardPage() {
  const { token } = useAuth();
  const [workflows, setWorkflows] = useState<WorkflowListItem[]>([]);
  const [workflowsError, setWorkflowsError] = useState<string | null>(null);

  useEffect(() => {
    if (!token) return;
    listWorkflows(token, { page_size: 5 })
      .then((res) => setWorkflows(res.items))
      .catch((err) => {
        setWorkflowsError(err instanceof Error ? err.message : "Failed to load active workflows.");
      });
  }, [token]);

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h2 className="text-2xl font-bold tracking-tight text-slate-50">
          Welcome back to <span className="text-brand-400">GraphForge</span>
        </h2>
        <p className="mt-1.5 max-w-2xl text-sm text-slate-400">
          What you're working on, what to do next, and what just happened.
        </p>
      </div>

      {/* What am I working on? */}
      {workflowsError && (
        <div className="rounded-lg border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-300">
          {workflowsError}
        </div>
      )}
      {workflows.length > 0 && (
        <Card
          title="Active SDLC Workflows"
          description="Engineering tasks progressing through the lifecycle"
          action={
            <Link
              to="/workflows/new"
              className="text-xs font-medium text-indigo-400 hover:text-indigo-300"
            >
              + New Workflow
            </Link>
          }
        >
          <div className="space-y-4">
            {workflows.map((w) => (
              <Link
                key={w.workflow_id}
                to={`/workflows/${w.workflow_id}`}
                className="block rounded-lg border border-slate-800 bg-slate-900/40 p-4 transition-colors hover:border-indigo-500/40 hover:bg-slate-900/60"
              >
                <div className="mb-3 flex items-center justify-between">
                  <h4 className="text-sm font-medium text-slate-100 truncate">{w.title}</h4>
                  <StatusBadge
                    label={w.status === "completed" ? "Complete" : "In Progress"}
                    tone={w.status === "completed" ? "success" : "info"}
                  />
                </div>
                <WorkflowTimeline stages={w.stages} currentStage={w.current_stage} />
              </Link>
            ))}
          </div>
        </Card>
      )}

      {/* What should I do next? */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <Link
          to="/workflows/new"
          className="group flex items-start gap-4 rounded-xl border border-slate-800 bg-slate-900/60 p-6 shadow-sm shadow-black/20 transition-colors hover:border-indigo-500/40 hover:bg-slate-900/80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500"
          aria-label="Start SDLC Workflow"
        >
          <div className="rounded-lg bg-indigo-500/10 p-3 ring-1 ring-inset ring-indigo-500/30">
            <GitMerge className="h-6 w-6 text-indigo-400" aria-hidden="true" />
          </div>
          <div>
            <h3 className="text-sm font-semibold text-slate-100 group-hover:text-indigo-300">
              SDLC Workflow
            </h3>
            <p className="mt-1 text-xs text-slate-400">
              Start a guided engineering lifecycle. Planning → Development → Testing → Review — each
              phase feeds the next.
            </p>
          </div>
        </Link>

        <Link
          to="/planning"
          className="group flex items-start gap-4 rounded-xl border border-slate-800 bg-slate-900/60 p-6 shadow-sm shadow-black/20 transition-colors hover:border-sky-500/40 hover:bg-slate-900/80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-500"
          aria-label="Planning Assistant"
        >
          <div className="rounded-lg bg-sky-500/10 p-3 ring-1 ring-inset ring-sky-500/30">
            <Lightbulb className="h-6 w-6 text-sky-400" aria-hidden="true" />
          </div>
          <div>
            <h3 className="text-sm font-semibold text-slate-100 group-hover:text-sky-300">
              Planning Assistant
            </h3>
            <p className="mt-1 text-xs text-slate-400">
              Describe an engineering task and get an AI-generated implementation plan grounded in
              your architecture graph. Every recommendation comes with verifiable evidence.
            </p>
          </div>
        </Link>

        <Link
          to="/review"
          className="group flex items-start gap-4 rounded-xl border border-slate-800 bg-slate-900/60 p-6 shadow-sm shadow-black/20 transition-colors hover:border-emerald-500/40 hover:bg-slate-900/80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-500"
          aria-label="Review Pull Request"
        >
          <div className="rounded-lg bg-emerald-500/10 p-3 ring-1 ring-inset ring-emerald-500/30">
            <Search className="h-6 w-6 text-emerald-400" aria-hidden="true" />
          </div>
          <div>
            <h3 className="text-sm font-semibold text-slate-100 group-hover:text-emerald-300">
              Review Pull Request
            </h3>
            <p className="mt-1 text-xs text-slate-400">
              Submit a GitHub PR URL for AI-powered change impact analysis. Get breaking changes,
              blast radius, and reviewers — all backed by graph evidence, never hallucinated.
            </p>
          </div>
        </Link>
      </div>
    </div>
  );
}
