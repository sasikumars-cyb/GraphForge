import { useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { ArrowLeft, GitMerge } from "lucide-react";
import { Card } from "../components/Card";
import { EvidencePanel } from "../components/EvidencePanel";
import { ConfidenceBadge } from "../components/agents/ConfidenceBadge";
import { RunProgress } from "../components/agents/RunProgress";
import { RunStatusBadge } from "../components/agents/RunStatusBadge";
import { useAuth } from "../app/auth-context";
import { getAgentRun } from "../lib/api/agentRuns";
import { stageLabel } from "../lib/workflowDerived";
import type { RunDetail } from "../types/agent";

export function RunDetailPage() {
  const { runId } = useParams<{ runId: string }>();
  const { token } = useAuth();
  const [run, setRun] = useState<RunDetail | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token || !runId) return;
    setIsLoading(true);
    getAgentRun(token, runId)
      .then(setRun)
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load run."))
      .finally(() => setIsLoading(false));
  }, [token, runId]);

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
      <div className="flex items-center gap-3">
        <RunStatusBadge status={run.status} />
        {step?.confidence && <ConfidenceBadge confidence={step.confidence} />}
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
        </dl>
      </Card>

      {/* Error */}
      {run.status === "failed" && run.error_message && (
        <div className="rounded-lg border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-300">
          <strong>Error:</strong> {run.error_message}
        </div>
      )}

      {/* Step result */}
      {step?.result && Object.keys(step.result).length > 0 && (
        <Card title="Agent Result">
          <pre className="max-h-96 overflow-auto rounded-lg bg-slate-950 p-4 text-xs text-slate-300">
            {JSON.stringify(step.result, null, 2)}
          </pre>
        </Card>
      )}

      {/* Evidence */}
      <EvidencePanel evidence={evidence} />
    </div>
  );
}
