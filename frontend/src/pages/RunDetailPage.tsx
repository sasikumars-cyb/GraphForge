import { useCallback, useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { ArrowLeft, GitMerge } from "lucide-react";
import { Card } from "../components/Card";
import { ConfidenceBadge } from "../components/agents/ConfidenceBadge";
import { RunProgress } from "../components/agents/RunProgress";
import { RunStatusBadge } from "../components/agents/RunStatusBadge";
import { StageResultPanel } from "../components/runs/StageResultPanel";
import { useAuth } from "../app/auth-context";
import { getAgentRun } from "../lib/api/agentRuns";
import { stageFromGoal, stageLabel } from "../lib/workflowDerived";
import type { RunDetail } from "../types/agent";

// Matches WorkflowPage's polling cadence — same "watch it happen" pattern,
// reusing the same GET this page already calls on mount. Now that run
// creation returns immediately (see app.orchestrator.background_execution
// on the backend) rather than blocking until the agent finishes, this is
// what actually shows progress and the eventual result — including on a
// fresh page load after a refresh/navigation-back, since it's driven
// entirely by re-fetching backend state, not any client-held state.
const POLL_INTERVAL_MS = 2500;

export function RunDetailPage() {
  const { runId } = useParams<{ runId: string }>();
  const { token } = useAuth();
  const [run, setRun] = useState<RunDetail | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadRun = useCallback(
    async (isInitial: boolean, signal?: AbortSignal) => {
      if (!token || !runId) return;
      if (isInitial) setIsLoading(true);
      try {
        const detail = await getAgentRun(token, runId, signal);
        setRun(detail);
        setError(null);
      } catch (err) {
        if (err instanceof DOMException && err.name === "AbortError") return;
        setError(err instanceof Error ? err.message : "Failed to load run.");
      } finally {
        if (isInitial) setIsLoading(false);
      }
    },
    [token, runId],
  );

  useEffect(() => {
    const controller = new AbortController();
    loadRun(true, controller.signal);
    return () => controller.abort();
  }, [loadRun]);

  // Poll while the run is still in flight. Stops on unmount (route change,
  // refresh, back/forward all just remount this page and re-fetch current
  // backend state — there's no client-side execution state to lose).
  const runStatus = run?.status;
  useEffect(() => {
    if (runStatus !== "queued" && runStatus !== "running") return;
    const controller = new AbortController();
    const id = window.setInterval(() => loadRun(false, controller.signal), POLL_INTERVAL_MS);
    return () => {
      window.clearInterval(id);
      controller.abort();
    };
  }, [runStatus, loadRun]);

  if (isLoading) {
    return (
      <div className="flex flex-col gap-6">
        <p className="text-sm text-slate-400">Loading run details…</p>
      </div>
    );
  }

  if (error || !run) {
    return (
      <div className="flex flex-col gap-6">
        <Link
          to="/runs"
          className="inline-flex items-center gap-1 text-sm text-slate-400 hover:text-slate-200"
        >
          <ArrowLeft className="h-4 w-4" aria-hidden="true" />
          Back to history
        </Link>
        <div className="rounded-lg border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-300">
          {error ?? "Run not found."}
        </div>
      </div>
    );
  }

  const step = run.steps[0];
  const evidence = step?.evidence ?? [];
  // workflow_stage is only set for stage runs; standalone runs (created
  // from /workspace/planning etc.) fall back to deriving it from the
  // goal so StageResultPanel still knows whether to render a blueprint.
  const stage = run.workflow_stage ?? stageFromGoal(run.goal);

  return (
    <div className="flex flex-col gap-6">
      <Link
        to="/runs"
        className="inline-flex items-center gap-1 text-sm text-slate-400 hover:text-slate-200"
      >
        <ArrowLeft className="h-4 w-4" aria-hidden="true" />
        Back to history
      </Link>

      {/* This run's Stage produced it as part of a larger Workflow — say so
          up front, since reaching a run directly (rather than through the
          Workflow page) otherwise reads as an isolated, archived record. */}
      {run.workflow_id && (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-brand-500/30 bg-brand-500/10 px-4 py-3 text-sm">
          <span className="flex items-center gap-2 text-brand-200">
            <GitMerge className="h-4 w-4 shrink-0" aria-hidden="true" />
            Part of workflow:{" "}
            <strong className="text-brand-100">
              {run.subject.display_name || run.subject.subject_id}
            </strong>
            {run.workflow_stage && (
              <span className="text-brand-300"> — {stageLabel(run.workflow_stage)} stage</span>
            )}
          </span>
          <Link
            to={`/workflows/${run.workflow_id}`}
            className="shrink-0 rounded-md bg-brand-500/20 px-3 py-1.5 text-xs font-medium text-brand-200 ring-1 ring-inset ring-brand-500/40 transition-colors hover:bg-brand-500/30"
          >
            Open full workflow →
          </Link>
        </div>
      )}

      {/* Progress for in-flight runs */}
      {(run.status === "queued" || run.status === "running") && (
        <Card>
          <RunProgress status={run.status} error={run.error_message} />
        </Card>
      )}

      {/* Header */}
      <div>
        <h2 className="text-xl font-semibold text-slate-50">
          {run.title ?? run.subject.display_name ?? run.subject.subject_id}
        </h2>
        <div className="mt-2 flex items-center gap-3">
          <RunStatusBadge status={run.status} />
          {step?.confidence && <ConfidenceBadge confidence={step.confidence} />}
        </div>
      </div>

      {/* Run metadata */}
      <Card title="Run Details">
        <dl className="grid grid-cols-2 gap-x-6 gap-y-3 text-sm sm:grid-cols-3 lg:grid-cols-4">
          <div>
            <dt className="text-xs text-slate-500">Run ID</dt>
            <dd className="truncate font-mono text-xs text-slate-300" title={run.run_id}>
              {run.run_id}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-slate-500">Goal</dt>
            <dd className="text-slate-200">{run.goal}</dd>
          </div>
          <div>
            <dt className="text-xs text-slate-500">Subject</dt>
            <dd className="truncate text-slate-200" title={run.subject.display_name}>
              {run.subject.display_name || run.subject.subject_id}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-slate-500">Type</dt>
            <dd className="text-slate-200">{run.subject.subject_type}</dd>
          </div>
          <div>
            <dt className="text-xs text-slate-500">Status</dt>
            <dd>
              <RunStatusBadge status={run.status} />
            </dd>
          </div>
          <div>
            <dt className="text-xs text-slate-500">Confidence</dt>
            <dd>
              {step?.confidence ? (
                <ConfidenceBadge confidence={step.confidence} showReasoning />
              ) : (
                <span className="text-slate-500">—</span>
              )}
            </dd>
          </div>
          {run.started_at && (
            <div>
              <dt className="text-xs text-slate-500">Started</dt>
              <dd className="text-slate-200">{new Date(run.started_at).toLocaleString()}</dd>
            </div>
          )}
          {run.completed_at && (
            <div>
              <dt className="text-xs text-slate-500">Completed</dt>
              <dd className="text-slate-200">{new Date(run.completed_at).toLocaleString()}</dd>
            </div>
          )}
          {step?.latency_ms != null && (
            <div>
              <dt className="text-xs text-slate-500">Duration</dt>
              <dd className="text-slate-200">{(step.latency_ms / 1000).toFixed(1)}s</dd>
            </div>
          )}
          {run.model && (
            <div>
              <dt className="text-xs text-slate-500">Model</dt>
              <dd className="text-slate-200">{run.model}</dd>
            </div>
          )}
          {run.provider && (
            <div>
              <dt className="text-xs text-slate-500">Provider</dt>
              <dd className="text-slate-200">{run.provider}</dd>
            </div>
          )}
          {run.user && (
            <div>
              <dt className="text-xs text-slate-500">User</dt>
              <dd className="text-slate-200">{run.user}</dd>
            </div>
          )}
          {run.repository && (
            <div>
              <dt className="text-xs text-slate-500">Repository</dt>
              <dd className="truncate text-slate-200" title={run.repository}>
                {run.repository}
              </dd>
            </div>
          )}
        </dl>
      </Card>

      {/* Error */}
      {run.status === "failed" && run.error_message && (
        <div className="rounded-lg border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-300">
          <strong>Error:</strong> {run.error_message}
        </div>
      )}

      {/* Step result — same tabbed panel WorkflowPage uses, so a run
          reached from Run History renders its blueprint graph exactly
          like it does inside a workflow, instead of a raw JSON dump. The
          panel's own Evidence tab replaces what used to be a separate,
          always-visible EvidencePanel below it. */}
      {step && (
        <StageResultPanel
          stage={stage}
          step={step}
          agentLabel={run.subject.display_name || run.goal}
          evidence={evidence}
        />
      )}
    </div>
  );
}
