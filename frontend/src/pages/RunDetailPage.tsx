import { useCallback, useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { ArrowLeft, GitMerge } from "lucide-react";
import { Card } from "../components/Card";
import { ConfidenceBadge } from "../components/agents/ConfidenceBadge";
import { RunProgress } from "../components/agents/RunProgress";
import { RunStatusBadge } from "../components/agents/RunStatusBadge";
import {
  pullRequestIdFromSubject,
  ViewVisualReportButton,
} from "../components/agents/ViewVisualReportButton";
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
        <p className="text-sm text-fg-muted">Loading run details…</p>
      </div>
    );
  }

  if (error || !run) {
    return (
      <div className="flex flex-col gap-6">
        <Link
          to="/runs"
          className="inline-flex items-center gap-1 text-sm text-fg-muted hover:text-fg-secondary"
        >
          <ArrowLeft className="h-4 w-4" aria-hidden="true" />
          Back to history
        </Link>
        <div className="rounded-lg border border-danger-line/30 bg-danger-bg px-4 py-3 text-sm text-danger-fg">
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

  // PR Review's "View Visual Report" has no equivalent in StageResultPanel,
  // because it isn't a blueprint diagram at all: it's a separate, pre-
  // rendered HTML dashboard keyed off the pull request id, not off
  // step.result. It used to render only inline in ReviewPage.tsx (the page
  // shown right after submitting a review); reaching the exact same
  // completed run through Run History (this page) landed on
  // StageResultPanel instead, which has no stage mapped for "review_pr"
  // (see GOAL_TO_STAGE) and therefore had no way to open it.
  const pullRequestId = pullRequestIdFromSubject(run.goal, run.subject.subject_id);

  return (
    <div className="flex flex-col gap-6">
      <Link
        to="/runs"
        className="inline-flex items-center gap-1 text-sm text-fg-muted hover:text-fg-secondary"
      >
        <ArrowLeft className="h-4 w-4" aria-hidden="true" />
        Back to history
      </Link>

      {/* This run's Stage produced it as part of a larger Workflow — say so
          up front, since reaching a run directly (rather than through the
          Workflow page) otherwise reads as an isolated, archived record. */}
      {run.workflow_id && (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-accent-line/30 bg-accent-bg px-4 py-3 text-sm">
          <span className="flex items-center gap-2 text-accent-fg">
            <GitMerge className="h-4 w-4 shrink-0" aria-hidden="true" />
            Part of workflow:{" "}
            <strong className="text-accent-fg">
              {run.subject.display_name || run.subject.subject_id}
            </strong>
            {run.workflow_stage && (
              <span className="text-accent-fg"> — {stageLabel(run.workflow_stage)} stage</span>
            )}
          </span>
          <Link
            to={`/workflows/${run.workflow_id}`}
            className="shrink-0 rounded-md bg-accent-bg px-3 py-1.5 text-xs font-medium text-accent-fg ring-1 ring-inset ring-accent-line/40 transition-colors hover:bg-accent-bg"
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
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-fg">
            {run.title ?? run.subject.display_name ?? run.subject.subject_id}
          </h1>
          <div className="mt-2 flex items-center gap-3">
            <RunStatusBadge status={run.status} />
            {step?.confidence && <ConfidenceBadge confidence={step.confidence} />}
          </div>
        </div>
        <ViewVisualReportButton pullRequestId={pullRequestId} />
      </div>

      {/* Run metadata */}
      <Card title="Run Details">
        <dl className="grid grid-cols-2 gap-x-6 gap-y-3 text-sm sm:grid-cols-3 lg:grid-cols-4">
          <div>
            <dt className="text-xs text-fg-muted">Run ID</dt>
            <dd className="truncate font-mono text-xs text-fg-secondary" title={run.run_id}>
              {run.run_id}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-fg-muted">Goal</dt>
            <dd className="text-fg-secondary">{run.goal}</dd>
          </div>
          <div>
            <dt className="text-xs text-fg-muted">Subject</dt>
            <dd className="truncate text-fg-secondary" title={run.subject.display_name}>
              {run.subject.display_name || run.subject.subject_id}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-fg-muted">Type</dt>
            <dd className="text-fg-secondary">{run.subject.subject_type}</dd>
          </div>
          <div>
            <dt className="text-xs text-fg-muted">Status</dt>
            <dd>
              <RunStatusBadge status={run.status} />
            </dd>
          </div>
          <div>
            <dt className="text-xs text-fg-muted">Confidence</dt>
            <dd>
              {step?.confidence ? (
                <ConfidenceBadge confidence={step.confidence} showReasoning />
              ) : (
                <span className="text-fg-muted">—</span>
              )}
            </dd>
          </div>
          {run.started_at && (
            <div>
              <dt className="text-xs text-fg-muted">Started</dt>
              <dd className="text-fg-secondary">{new Date(run.started_at).toLocaleString()}</dd>
            </div>
          )}
          {run.completed_at && (
            <div>
              <dt className="text-xs text-fg-muted">Completed</dt>
              <dd className="text-fg-secondary">{new Date(run.completed_at).toLocaleString()}</dd>
            </div>
          )}
          {step?.latency_ms != null && (
            <div>
              <dt className="text-xs text-fg-muted">Duration</dt>
              <dd className="text-fg-secondary">{(step.latency_ms / 1000).toFixed(1)}s</dd>
            </div>
          )}
          {run.model && (
            <div>
              <dt className="text-xs text-fg-muted">Model</dt>
              <dd className="text-fg-secondary">{run.model}</dd>
            </div>
          )}
          {run.provider && (
            <div>
              <dt className="text-xs text-fg-muted">Provider</dt>
              <dd className="text-fg-secondary">{run.provider}</dd>
            </div>
          )}
          {run.user && (
            <div>
              <dt className="text-xs text-fg-muted">User</dt>
              <dd className="text-fg-secondary">{run.user}</dd>
            </div>
          )}
          {run.repository && (
            <div>
              {/* UX audit P1.1: `run.repository` is `repositories_consulted`
                  (grounding scope — everything the graph traversal read for
                  context), not the repositories this change actually
                  affects — those are two different numbers (e.g. 17
                  consulted vs. 3 affected) and showing the consulted list
                  under an unqualified "Repository" label read as "this run
                  touches 17 repos". The full names still live in one place
                  — the Summary tab's GroundingBanner — this is a count and
                  a pointer to it, not a second copy of the list. */}
              <dt className="text-xs text-fg-muted">Grounding Scope</dt>
              <dd className="truncate text-fg-secondary" title={run.repository}>
                {(() => {
                  const repoNames = run.repository.split(", ").filter(Boolean);
                  return repoNames.length === 1
                    ? repoNames[0]
                    : `${repoNames.length} repositories consulted — see Summary`;
                })()}
              </dd>
            </div>
          )}
        </dl>
      </Card>

      {/* Error */}
      {run.status === "failed" && run.error_message && (
        <div className="rounded-lg border border-danger-line/30 bg-danger-bg px-4 py-3 text-sm text-danger-fg">
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
