import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { Code2, Send, RotateCcw, History } from "lucide-react";
import { Card } from "../components/Card";
import { EvidencePanel } from "../components/EvidencePanel";
import { ConfidenceBadge } from "../components/agents/ConfidenceBadge";
import { RunProgress } from "../components/agents/RunProgress";
import { RunStatusBadge } from "../components/agents/RunStatusBadge";
import { DevelopmentResultDetails } from "../components/agents/StageResultDetails";
import {
  PlanningRunPicker,
  StandaloneContextBanner,
} from "../components/agents/StandalonePlanningContext";
import { useAgentRun } from "../hooks/useAgentRun";
import type { DevelopmentPlanResult } from "../types/agent";

const EXAMPLES = [
  "Implement JWT authentication for all services",
  "Split OrderService into separate command and query services",
  "Introduce Redis caching for payment processing",
  "Add retry support with exponential backoff for payment-service",
];

export function DevelopmentPage() {
  const [input, setInput] = useState("");
  const [planningRunId, setPlanningRunId] = useState<string | null>(null);
  const { run, isSubmitting, error, submit, reset } = useAgentRun();

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    const trimmed = input.trim();
    if (!trimmed) return;
    submit({
      subject_reference: trimmed,
      goal: "develop_change_plan",
      ...(planningRunId ? { planning_run_id: planningRunId } : {}),
    });
  };

  const handleExampleClick = (example: string) => {
    setInput(example);
  };

  const handleNewPlan = () => {
    reset();
    setInput("");
    setPlanningRunId(null);
  };

  const hasResult =
    run && (run.status === "completed" || run.status === "partial" || run.status === "failed");

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="rounded-lg bg-cat-7-bg p-2 ring-1 ring-inset ring-cat-7-line/30">
            <Code2 className="h-5 w-5 text-cat-7-fg" aria-hidden="true" />
          </div>
          <div>
            <h1 className="text-xl font-semibold text-fg">Development Agent</h1>
            <p className="text-sm text-fg-muted">
              Describe an engineering change. GraphForge produces a structured implementation
              blueprint — repositories, components, phases, risks — grounded in your architecture
              graph.
            </p>
          </div>
        </div>
        <Link
          to="/runs"
          className="flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium text-fg-muted ring-1 ring-inset ring-line transition-colors hover:bg-surface-raised hover:text-fg-secondary"
          aria-label="View run history"
        >
          <History className="h-3.5 w-3.5" aria-hidden="true" />
          History
        </Link>
      </div>

      {/* Input Form */}
      {!hasResult && (
        <Card>
          <form onSubmit={handleSubmit} className="flex flex-col gap-4">
            <StandaloneContextBanner planningRunId={planningRunId} />

            <div>
              <label
                htmlFor="development-input"
                className="block text-sm font-medium text-fg-secondary"
              >
                What would you like to implement?
              </label>
              <textarea
                id="development-input"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                disabled={isSubmitting}
                placeholder="Describe your engineering change, feature, or refactoring goal…"
                rows={4}
                className="mt-2 w-full rounded-lg border border-line bg-surface-raised px-4 py-3 text-sm text-fg placeholder-fg-subtle focus:border-cat-7-line disabled:opacity-50"
                aria-required="true"
              />
            </div>

            <PlanningRunPicker
              value={planningRunId}
              onChange={setPlanningRunId}
              disabled={isSubmitting}
            />

            {/* Examples */}
            {!isSubmitting && !run && (
              <div>
                <p className="mb-2 text-xs font-medium text-fg-muted">Try an example:</p>
                <div className="flex flex-wrap gap-2">
                  {EXAMPLES.map((example) => (
                    <button
                      key={example}
                      type="button"
                      onClick={() => handleExampleClick(example)}
                      className="rounded-md border border-line px-2.5 py-1 text-xs text-fg-muted transition-colors hover:border-cat-7-line/40 hover:text-cat-7-fg"
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
                className="inline-flex items-center gap-2 rounded-lg bg-accent-solid px-4 py-2 text-sm font-medium text-accent-on-solid transition-colors hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50"
                aria-label="Submit development request"
              >
                <Send className="h-4 w-4" aria-hidden="true" />
                {isSubmitting ? "Analyzing…" : "Generate Blueprint"}
              </button>
              {isSubmitting && (
                <span className="text-xs text-fg-muted">This may take up to a minute.</span>
              )}
            </div>
          </form>
        </Card>
      )}

      {/* Progress */}
      {run && !hasResult && (
        <Card>
          <RunProgress status={run.status} error={run.error_message} />
        </Card>
      )}

      {/* Error */}
      {error && (
        <div className="rounded-lg border border-danger-line/30 bg-danger-bg px-4 py-3 text-sm text-danger-fg">
          {error}
          <button
            type="button"
            onClick={handleNewPlan}
            className="ml-3 text-danger-fg underline hover:text-danger-fg"
          >
            Try again
          </button>
        </div>
      )}

      {/* Result */}
      {hasResult && (
        <DevelopmentResultView
          run={run}
          onNewPlan={handleNewPlan}
          groundedInPlanningRunId={planningRunId}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Result sub-component
// ---------------------------------------------------------------------------

function DevelopmentResultView({
  run,
  onNewPlan,
  groundedInPlanningRunId,
}: {
  run: NonNullable<ReturnType<typeof useAgentRun>["run"]>;
  onNewPlan: () => void;
  groundedInPlanningRunId: string | null;
}) {
  const step = run.steps[0];
  const result = step?.result as unknown as DevelopmentPlanResult | undefined;
  const evidence = step?.evidence ?? [];

  return (
    <div className="flex flex-col gap-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <RunStatusBadge status={run.status} />
          {step?.confidence && <ConfidenceBadge confidence={step.confidence} showReasoning />}
          {groundedInPlanningRunId && (
            <span className="rounded-full bg-success-bg px-2.5 py-0.5 text-xs font-medium text-success-fg ring-1 ring-inset ring-success-line/30">
              Grounded in a Planning run
            </span>
          )}
        </div>
        <button
          type="button"
          onClick={onNewPlan}
          className="inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium text-fg-muted ring-1 ring-inset ring-line transition-colors hover:bg-surface-raised hover:text-fg-secondary"
        >
          <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />
          New Blueprint
        </button>
      </div>

      {/* Run metadata */}
      <Card title="Run Details">
        <dl className="grid grid-cols-2 gap-x-6 gap-y-3 text-sm sm:grid-cols-3 lg:grid-cols-4">
          <div>
            <dt className="text-xs text-fg-muted">Goal</dt>
            <dd className="text-fg-secondary">Change Plan</dd>
          </div>
          <div>
            <dt className="text-xs text-fg-muted">Subject</dt>
            <dd className="truncate text-fg-secondary" title={run.subject.display_name}>
              {run.subject.display_name}
            </dd>
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
                <ConfidenceBadge confidence={step.confidence} />
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
        </dl>
      </Card>

      {/* Error */}
      {run.status === "failed" && run.error_message && (
        <div className="rounded-lg border border-danger-line/30 bg-danger-bg px-4 py-3 text-sm text-danger-fg">
          <strong>Error:</strong> {run.error_message}
        </div>
      )}

      {/* Development Plan Result */}
      {result && <DevelopmentResultDetails result={result} />}

      {/* Evidence */}
      <EvidencePanel evidence={evidence} />
    </div>
  );
}
