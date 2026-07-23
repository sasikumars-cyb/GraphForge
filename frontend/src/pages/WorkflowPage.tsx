import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useParams, Link } from "react-router-dom";
import { ArrowLeft, GitMerge, Send, RotateCcw } from "lucide-react";
import { Card } from "../components/Card";
import { EvidencePanel } from "../components/EvidencePanel";
import { ConfidenceBadge } from "../components/agents/ConfidenceBadge";
import { RunStatusBadge } from "../components/agents/RunStatusBadge";
import { WorkflowTimeline } from "../components/workflow/WorkflowTimeline";
import { StageNavigation } from "../components/workflow/StageNavigation";
import { useAuth } from "../app/auth-context";
import { getWorkflow } from "../lib/api/workflows";
import { continueWorkflow, createWorkflow } from "../lib/api/workflows";
import { getAgentRun } from "../lib/api/agentRuns";
import type { RunDetail, WorkflowDetail } from "../types/agent";

const STAGE_ORDER = ["planning", "development", "testing", "review"];

function nextStageAfter(current: string): string | null {
  const idx = STAGE_ORDER.indexOf(current);
  if (idx === -1 || idx + 1 >= STAGE_ORDER.length) return null;
  return STAGE_ORDER[idx + 1];
}

export function WorkflowPage() {
  const { workflowId } = useParams<{ workflowId: string }>();
  const { token } = useAuth();
  const [workflow, setWorkflow] = useState<WorkflowDetail | null>(null);
  const [selectedRun, setSelectedRun] = useState<RunDetail | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Fetch workflow
  const loadWorkflow = useCallback(async () => {
    if (!token || !workflowId) return;
    try {
      const detail = await getWorkflow(token, workflowId);
      setWorkflow(detail);
      // Auto-load the latest completed run's detail
      const latestRun = detail.runs[detail.runs.length - 1];
      if (latestRun) {
        const runDetail = await getAgentRun(token, latestRun.run_id);
        setSelectedRun(runDetail);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load workflow.");
    } finally {
      setIsLoading(false);
    }
  }, [token, workflowId]);

  useEffect(() => {
    loadWorkflow();
  }, [loadWorkflow]);

  const handleContinue = async () => {
    if (!token || !workflowId) return;
    setIsSubmitting(true);
    setError(null);
    try {
      const response = await continueWorkflow(token, workflowId);
      // Reload workflow to get updated state
      const detail = await getWorkflow(token, workflowId);
      setWorkflow(detail);
      // Load the new run
      const runDetail = await getAgentRun(token, response.run_id);
      setSelectedRun(runDetail);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to continue workflow.");
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleStageClick = async (runId: string) => {
    if (!token) return;
    try {
      const detail = await getAgentRun(token, runId);
      setSelectedRun(detail);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load run detail.");
    }
  };

  if (isLoading) {
    return (
      <div className="flex flex-col gap-6">
        <p className="text-sm text-slate-400">Loading workflow…</p>
      </div>
    );
  }

  if (error && !workflow) {
    return (
      <div className="flex flex-col gap-6">
        <Link
          to="/"
          className="inline-flex items-center gap-1 text-sm text-slate-400 hover:text-slate-200"
        >
          <ArrowLeft className="h-4 w-4" aria-hidden="true" />
          Back to dashboard
        </Link>
        <div className="rounded-lg border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-300">
          {error}
        </div>
      </div>
    );
  }

  if (!workflow) return null;

  const lastCompletedStage = [...workflow.stages].reverse().find((s) => s.status === "completed");
  const canContinue =
    workflow.status === "in_progress" &&
    lastCompletedStage &&
    workflow.current_stage !== "completed" &&
    workflow.current_stage in Object.fromEntries(STAGE_ORDER.map((s) => [s, true]));

  const step = selectedRun?.steps[0];

  return (
    <div className="flex flex-col gap-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="rounded-lg bg-indigo-500/10 p-2 ring-1 ring-inset ring-indigo-500/30">
            <GitMerge className="h-5 w-5 text-indigo-400" aria-hidden="true" />
          </div>
          <div>
            <h2 className="text-xl font-semibold text-slate-50">{workflow.title}</h2>
            <p className="text-sm text-slate-400">
              SDLC Workflow •{" "}
              {workflow.status === "completed" ? "Complete" : `Stage: ${workflow.current_stage}`}
            </p>
          </div>
        </div>
        <Link
          to="/"
          className="flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium text-slate-400 ring-1 ring-inset ring-slate-700 transition-colors hover:bg-slate-800 hover:text-slate-200"
        >
          Dashboard
        </Link>
      </div>

      {/* Timeline */}
      <Card>
        <WorkflowTimeline stages={workflow.stages} currentStage={workflow.current_stage} />
      </Card>

      {/* Error */}
      {error && (
        <div className="rounded-lg border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-300">
          {error}
        </div>
      )}

      {/* Continue action */}
      {canContinue && (
        <StageNavigation
          nextStage={workflow.current_stage}
          isSubmitting={isSubmitting}
          onContinue={handleContinue}
        />
      )}

      {workflow.status === "completed" && (
        <StageNavigation nextStage="completed" isSubmitting={false} onContinue={() => {}} />
      )}

      {/* Stage runs */}
      {workflow.runs.length > 1 && (
        <div className="flex flex-wrap gap-2">
          {workflow.runs.map((r) => (
            <button
              key={r.run_id}
              type="button"
              onClick={() => handleStageClick(r.run_id)}
              className={`rounded-md px-3 py-1.5 text-xs font-medium ring-1 ring-inset transition-colors ${
                selectedRun?.run_id === r.run_id
                  ? "bg-sky-500/20 text-sky-300 ring-sky-500/40"
                  : "text-slate-400 ring-slate-700 hover:bg-slate-800 hover:text-slate-200"
              }`}
            >
              {r.workflow_stage ?? r.goal}
            </button>
          ))}
        </div>
      )}

      {/* Selected run result */}
      {selectedRun && step && (
        <>
          <Card title={`${selectedRun.workflow_stage ?? selectedRun.goal} — Result`}>
            <div className="flex items-center gap-3 mb-4">
              <RunStatusBadge status={selectedRun.status} />
              {step.confidence && <ConfidenceBadge confidence={step.confidence} showReasoning />}
            </div>
            {step.result && Object.keys(step.result).length > 0 && (
              <>
                {step.result.executive_summary && (
                  <p className="text-sm text-slate-200 mb-3">
                    {step.result.executive_summary as string}
                  </p>
                )}
                <details className="group">
                  <summary className="cursor-pointer text-xs font-medium text-slate-400 hover:text-slate-200">
                    Full result JSON
                  </summary>
                  <pre className="mt-2 max-h-64 overflow-auto rounded-lg bg-slate-950 p-3 text-xs text-slate-300">
                    {JSON.stringify(step.result, null, 2)}
                  </pre>
                </details>
              </>
            )}
          </Card>
          <EvidencePanel evidence={step.evidence} />
        </>
      )}
    </div>
  );
}

// --- New Workflow creation page ---

export function NewWorkflowPage() {
  const { token } = useAuth();
  const [input, setInput] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    const trimmed = input.trim();
    if (!trimmed || !token) return;

    setIsSubmitting(true);
    setError(null);
    try {
      const response = await createWorkflow(token, { title: trimmed });
      // Navigate to workflow page
      window.location.href = `/workflows/${response.workflow_id}`;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create workflow.");
      setIsSubmitting(false);
    }
  };

  const EXAMPLES = [
    "Implement JWT authentication across all microservices",
    "Migrate payment service from REST to event-driven architecture",
    "Add distributed tracing with OpenTelemetry",
    "Refactor order service to support multi-tenancy",
  ];

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center gap-3">
        <div className="rounded-lg bg-indigo-500/10 p-2 ring-1 ring-inset ring-indigo-500/30">
          <GitMerge className="h-5 w-5 text-indigo-400" aria-hidden="true" />
        </div>
        <div>
          <h2 className="text-xl font-semibold text-slate-50">New SDLC Workflow</h2>
          <p className="text-sm text-slate-400">
            Start a guided engineering lifecycle. Each phase feeds the next automatically.
          </p>
        </div>
      </div>

      <Card>
        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          <div>
            <label htmlFor="workflow-input" className="block text-sm font-medium text-slate-200">
              What engineering task do you want to deliver?
            </label>
            <textarea
              id="workflow-input"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              disabled={isSubmitting}
              placeholder="Describe the engineering objective. GraphForge will guide you through Planning → Development → Testing → Review."
              rows={4}
              className="mt-2 w-full rounded-lg border border-slate-700 bg-slate-800/60 px-4 py-3 text-sm text-slate-100 placeholder-slate-500 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500 disabled:opacity-50"
              aria-required="true"
            />
          </div>

          {!isSubmitting && (
            <div>
              <p className="mb-2 text-xs font-medium text-slate-500">Try an example:</p>
              <div className="flex flex-wrap gap-2">
                {EXAMPLES.map((example) => (
                  <button
                    key={example}
                    type="button"
                    onClick={() => setInput(example)}
                    className="rounded-md border border-slate-700 px-2.5 py-1 text-xs text-slate-400 transition-colors hover:border-indigo-500/40 hover:text-indigo-300"
                  >
                    {example}
                  </button>
                ))}
              </div>
            </div>
          )}

          <div className="flex items-center gap-3">
            <button
              type="submit"
              disabled={isSubmitting || !input.trim()}
              className="inline-flex items-center gap-2 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-50"
              aria-label="Start SDLC workflow"
            >
              <Send className="h-4 w-4" aria-hidden="true" />
              {isSubmitting ? "Starting…" : "Start Workflow"}
            </button>
            {isSubmitting && (
              <span className="text-xs text-slate-500">
                Starting planning phase — this may take up to a minute.
              </span>
            )}
          </div>
        </form>
      </Card>

      {error && (
        <div className="rounded-lg border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-300">
          {error}
        </div>
      )}
    </div>
  );
}
