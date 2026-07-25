import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { useParams, useSearchParams, Link } from "react-router-dom";
import { GitMerge, Send, Clapperboard } from "lucide-react";
import { Card } from "../components/Card";
import { RunStatusBadge } from "../components/agents/RunStatusBadge";
import { PipelineGraph } from "../components/workflow/PipelineGraph";
import { WorkflowHeader } from "../components/workflow/WorkflowHeader";
import { AgentActivityFeed } from "../components/workflow/AgentActivityFeed";
import { AgentCollaborationFlow } from "../components/workflow/AgentCollaborationFlow";
import { StageArtifactCard } from "../components/workflow/StageArtifactCard";
import { ApprovalGateBanner } from "../components/workflow/ApprovalGateBanner";
import { WorkflowApprovalBanner } from "../components/workflow/WorkflowApprovalBanner";
import { WorkflowSummaryHero } from "../components/workflow/WorkflowSummaryHero";
import { WorkflowReplayPanel } from "../components/workflow/WorkflowReplayPanel";
import { StageResultPanel } from "../components/runs/StageResultPanel";
import { useAuth } from "../app/auth-context";
import {
  approveWorkflow,
  continueWorkflow,
  createWorkflow,
  getWorkflow,
  rejectWorkflow,
} from "../lib/api/workflows";
import { getAgentRun } from "../lib/api/agentRuns";
import type { AgentStep, RunDetail, WorkflowDetail } from "../types/agent";
import { deriveWorkflowState, STAGE_AGENT_LABEL } from "../lib/workflowDerived";

const POLL_INTERVAL_MS = 2500;

export function WorkflowPage() {
  const { workflowId } = useParams<{ workflowId: string }>();
  const { token } = useAuth();
  const [workflow, setWorkflow] = useState<WorkflowDetail | null>(null);
  const [runsById, setRunsById] = useState<Map<string, RunDetail>>(new Map());
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showReplay, setShowReplay] = useState(false);
  const hasSelectedRef = useRef(false);

  const loadWorkflow = useCallback(
    async (isInitial: boolean) => {
      if (!token || !workflowId) return;
      try {
        const detail = await getWorkflow(token, workflowId);
        setWorkflow(detail);

        const runDetails = await Promise.all(detail.runs.map((r) => getAgentRun(token, r.run_id)));
        setRunsById(new Map(runDetails.map((r) => [r.run_id, r])));

        if (!hasSelectedRef.current && runDetails.length > 0) {
          hasSelectedRef.current = true;
          setSelectedRunId(runDetails[runDetails.length - 1].run_id);
        }
        if (isInitial) setError(null);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load workflow.");
      } finally {
        setIsLoading(false);
      }
    },
    [token, workflowId],
  );

  useEffect(() => {
    loadWorkflow(true);
  }, [loadWorkflow]);

  // Feature 9 / "watch it happen": poll for live updates while running,
  // reusing the same GET /workflows/{id} the page already calls — no new
  // endpoint, no websocket, just the existing resource refreshed on a timer.
  useEffect(() => {
    if (workflow?.status !== "in_progress") return;
    const id = window.setInterval(() => loadWorkflow(false), POLL_INTERVAL_MS);
    return () => window.clearInterval(id);
  }, [workflow?.status, loadWorkflow]);

  const handleApprove = async () => {
    if (!token || !workflowId) return;
    setIsSubmitting(true);
    setError(null);
    try {
      const response = await continueWorkflow(token, workflowId);
      const detail = await getWorkflow(token, workflowId);
      setWorkflow(detail);
      const runDetail = await getAgentRun(token, response.run_id);
      setRunsById((prev) => new Map(prev).set(runDetail.run_id, runDetail));
      setSelectedRunId(runDetail.run_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to continue workflow.");
      // The backend already persisted a failed Run and left current_stage
      // pointed at it before this request threw — without this, the header/
      // pipeline/banner would show stale pre-attempt state until the next
      // 2.5s poll tick, which is exactly the kind of contradiction this
      // page should never show.
      await loadWorkflow(false);
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleApproveWorkflow = async () => {
    if (!token || !workflowId) return;
    setIsSubmitting(true);
    setError(null);
    try {
      await approveWorkflow(token, workflowId);
      await loadWorkflow(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to approve workflow.");
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleRejectWorkflow = async () => {
    if (!token || !workflowId) return;
    setIsSubmitting(true);
    setError(null);
    try {
      await rejectWorkflow(token, workflowId);
      await loadWorkflow(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to reject workflow.");
    } finally {
      setIsSubmitting(false);
    }
  };

  if (isLoading) {
    return <WorkflowPageSkeleton />;
  }

  if (error && !workflow) {
    return (
      <div className="flex flex-col gap-6">
        <Link
          to="/"
          className="inline-flex items-center gap-1 text-sm text-slate-400 hover:text-slate-200"
        >
          ← Back to dashboard
        </Link>
        <div className="rounded-lg border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-300">
          {error}
        </div>
      </div>
    );
  }

  if (!workflow) return null;

  const stepsByRunId = new Map<string, AgentStep>();
  runsById.forEach((run, runId) => {
    if (run.steps[0]) stepsByRunId.set(runId, run.steps[0]);
  });

  const completedSteps = [...stepsByRunId.values()].filter((s) => s.status === "completed");
  const { phase, lastCompletedStage, currentStageInfo } = deriveWorkflowState(workflow);
  const canContinue = phase === "awaiting_approval";
  const failedRun = currentStageInfo?.run_id ? runsById.get(currentStageInfo.run_id) : undefined;

  const selectedRun = selectedRunId ? runsById.get(selectedRunId) : undefined;
  const selectedStage = selectedRun?.workflow_stage ?? null;
  const selectedStep = selectedRun?.steps[0];
  const selectedLabel = selectedStage
    ? (STAGE_AGENT_LABEL[selectedStage] ?? selectedStage)
    : selectedRun?.goal;

  return (
    <div className="flex flex-col gap-6">
      <WorkflowHeader workflow={workflow} completedSteps={completedSteps} phase={phase} />

      {(workflow.status === "completed" || workflow.status === "approved") && (
        <WorkflowSummaryHero
          workflow={workflow}
          steps={[...stepsByRunId.values()]}
          variant={workflow.status === "approved" ? "approved" : "completed"}
        />
      )}

      <Card
        title="Pipeline"
        action={
          completedSteps.length > 0 && (
            <button
              type="button"
              onClick={() => setShowReplay((v) => !v)}
              aria-expanded={showReplay}
              className={`inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-medium ring-1 ring-inset transition-colors ${
                showReplay
                  ? "bg-brand-500/20 text-brand-200 ring-brand-500/40"
                  : "text-slate-400 ring-slate-700 hover:bg-slate-800 hover:text-slate-200"
              }`}
            >
              <Clapperboard className="h-3.5 w-3.5" aria-hidden="true" />
              {showReplay ? "Hide Replay" : "Replay Execution"}
            </button>
          )
        }
      >
        <PipelineGraph
          stages={workflow.stages}
          selectedRunId={selectedRunId}
          onSelectStage={setSelectedRunId}
        />
      </Card>

      {showReplay && (
        <Card
          title="Workflow Replay"
          description="Watch the whole run play back, stage by stage, from its real event history"
        >
          <WorkflowReplayPanel stages={workflow.stages} stepsByRunId={stepsByRunId} />
        </Card>
      )}

      {error && phase !== "failed" && (
        <div className="rounded-lg border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-300">
          {error}
        </div>
      )}

      {canContinue && lastCompletedStage && (
        <ApprovalGateBanner
          completedStage={lastCompletedStage.stage}
          nextStage={workflow.current_stage}
          workflowTitle={workflow.title}
          isSubmitting={isSubmitting}
          onApprove={handleApprove}
        />
      )}

      {phase === "failed" && currentStageInfo && (
        <ApprovalGateBanner
          completedStage={lastCompletedStage?.stage ?? currentStageInfo.stage}
          nextStage={currentStageInfo.stage}
          workflowTitle={workflow.title}
          isSubmitting={isSubmitting}
          onApprove={handleApprove}
          failure={{
            stage: currentStageInfo.stage,
            errorMessage: failedRun?.error_message ?? null,
          }}
        />
      )}

      {phase === "blueprint_approval" && (
        <WorkflowApprovalBanner
          workflowTitle={workflow.title}
          status={workflow.status}
          isSubmitting={isSubmitting}
          onApprove={handleApproveWorkflow}
          onReject={handleRejectWorkflow}
        />
      )}

      <div className="grid gap-6 lg:grid-cols-[1fr_1.3fr]">
        <div className="flex flex-col gap-6">
          <Card title="Agent Activity" description="Live evidence as each agent works">
            <AgentActivityFeed stages={workflow.stages} stepsByRunId={stepsByRunId} />
          </Card>
          <Card title="Agent Collaboration" description="What each stage hands to the next">
            <AgentCollaborationFlow stages={workflow.stages} stepsByRunId={stepsByRunId} />
          </Card>
        </div>

        <div className="flex min-w-0 flex-col gap-4">
          {workflow.runs.length > 1 && (
            <div className="flex flex-wrap gap-2">
              {workflow.runs.map((r) => (
                <button
                  key={r.run_id}
                  type="button"
                  onClick={() => setSelectedRunId(r.run_id)}
                  className={`rounded-md px-3 py-1.5 text-xs font-medium ring-1 ring-inset transition-colors ${
                    selectedRunId === r.run_id
                      ? "bg-brand-500/20 text-brand-200 ring-brand-500/40"
                      : "text-slate-400 ring-slate-700 hover:bg-slate-800 hover:text-slate-200"
                  }`}
                >
                  {STAGE_AGENT_LABEL[r.workflow_stage ?? ""] ?? r.workflow_stage ?? r.goal}
                </button>
              ))}
            </div>
          )}

          {selectedRun && selectedStep && (
            <>
              <Card title={`${selectedLabel} — Artifacts`}>
                <div className="mb-3">
                  <RunStatusBadge status={selectedRun.status} />
                </div>
                <StageArtifactCard
                  stage={selectedStage ?? ""}
                  step={selectedStep}
                  stages={workflow.stages}
                />
                {selectedRun.error_message && (
                  <details className="group mt-3">
                    <summary className="cursor-pointer text-xs font-medium text-rose-300/80 hover:text-rose-200">
                      View error details
                    </summary>
                    <p className="mt-2 rounded-lg bg-slate-950 p-3 font-mono text-xs whitespace-pre-wrap text-rose-300">
                      {selectedRun.error_message}
                    </p>
                  </details>
                )}
              </Card>

              <StageResultPanel
                stage={selectedStage}
                step={selectedStep}
                agentLabel={selectedLabel ?? "Agent"}
                evidence={selectedStep.evidence}
              />
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function WorkflowPageSkeleton() {
  return (
    <div className="flex flex-col gap-6" aria-busy="true" aria-label="Loading workflow">
      <div className="h-40 animate-pulse rounded-2xl bg-slate-900" />
      <div className="h-28 animate-pulse rounded-xl bg-slate-900" />
      <div className="grid gap-6 lg:grid-cols-[1fr_1.3fr]">
        <div className="h-64 animate-pulse rounded-xl bg-slate-900" />
        <div className="h-64 animate-pulse rounded-xl bg-slate-900" />
      </div>
    </div>
  );
}

// --- New Workflow creation page ---

// Matches CreateWorkflowRequest.title's max_length on the backend — kept
// in sync manually since there's no shared schema between the two. A full
// multi-paragraph brief (requirements list, output schema, constraints)
// fits comfortably; this just stops an accidental paste of something huge.
const MAX_OBJECTIVE_LENGTH = 8000;

export function NewWorkflowPage() {
  const { token } = useAuth();
  const [searchParams] = useSearchParams();
  const [input, setInput] = useState(() => searchParams.get("title") ?? "");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    const trimmed = input.trim();
    if (!trimmed || !token) return;

    setIsSubmitting(true);
    setError(null);
    try {
      // "planning" is the only creatable type today — Auto Execution is
      // shown (disabled) to preview the direction, not to be selectable.
      const response = await createWorkflow(token, { title: trimmed, workflow_type: "planning" });
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
        <div className="rounded-lg bg-brand-500/10 p-2 ring-1 ring-inset ring-brand-500/30">
          <GitMerge className="h-5 w-5 text-brand-400" aria-hidden="true" />
        </div>
        <div>
          <h2 className="text-xl font-semibold text-slate-50">Describe what you want built</h2>
          <p className="text-sm text-slate-400">
            GraphForge turns this into an AI workflow — Planning → Development → Testing → Review —
            and runs each stage for you.
          </p>
        </div>
      </div>

      <fieldset className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <legend className="sr-only">Workflow type</legend>
        <button
          type="button"
          aria-pressed="true"
          className="flex flex-col items-start gap-1.5 rounded-xl border-2 border-brand-500 bg-brand-500/5 p-4 text-left"
        >
          <div className="flex flex-wrap items-center gap-2">
            <span
              className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full border-2 border-brand-400"
              aria-hidden="true"
            >
              <span className="h-1.5 w-1.5 rounded-full bg-brand-400" />
            </span>
            <span className="text-sm font-semibold text-slate-100">Planning Workflow</span>
            <span className="rounded-full bg-brand-500/20 px-2 py-0.5 text-[10px] font-semibold tracking-wide text-brand-300 uppercase">
              Recommended
            </span>
          </div>
          <p className="pl-6 text-xs text-slate-400">
            Plan, blueprint, test-strategize, and get an Engineering Review — no code is written and
            nothing is committed. You approve the result.
          </p>
        </button>

        <div
          aria-disabled="true"
          className="flex flex-col items-start gap-1.5 rounded-xl border border-slate-800 bg-slate-900/30 p-4 text-left opacity-60"
        >
          <div className="flex flex-wrap items-center gap-2">
            <span
              className="h-4 w-4 shrink-0 rounded-full border-2 border-slate-700"
              aria-hidden="true"
            />
            <span className="text-sm font-semibold text-slate-400">Implementation Workflow</span>
            <span className="rounded-full bg-slate-800 px-2 py-0.5 text-[10px] font-semibold tracking-wide text-slate-500 uppercase">
              Coming soon
            </span>
          </div>
          <p className="pl-6 text-xs text-slate-500">
            Writes code, opens a pull request, and runs an AI review on the real diff — not
            available yet.
          </p>
        </div>
      </fieldset>

      <Card>
        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          <div>
            <label htmlFor="workflow-input" className="block text-sm font-medium text-slate-200">
              What's the engineering objective?
            </label>
            <textarea
              id="workflow-input"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              disabled={isSubmitting}
              placeholder="e.g. Add rate limiting to the payment API. GraphForge will plan it, generate an implementation blueprint, propose tests, and review it — automatically."
              rows={4}
              maxLength={MAX_OBJECTIVE_LENGTH}
              className="mt-2 w-full rounded-lg border border-slate-700 bg-slate-800/60 px-4 py-3 text-sm text-slate-100 placeholder-slate-500 focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500 disabled:opacity-50"
              aria-required="true"
              aria-describedby="workflow-input-count"
            />
            <p
              id="workflow-input-count"
              className={`mt-1 text-right text-xs ${
                input.length >= MAX_OBJECTIVE_LENGTH
                  ? "text-rose-400"
                  : input.length >= MAX_OBJECTIVE_LENGTH * 0.9
                    ? "text-amber-400"
                    : "text-slate-500"
              }`}
            >
              {input.length.toLocaleString()} / {MAX_OBJECTIVE_LENGTH.toLocaleString()}
            </p>
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
                    className="rounded-md border border-slate-700 px-2.5 py-1 text-xs text-slate-400 transition-colors hover:border-brand-500/40 hover:text-brand-300"
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
              className="inline-flex items-center gap-2 rounded-lg bg-brand-500 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-brand-400 disabled:cursor-not-allowed disabled:opacity-50"
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
