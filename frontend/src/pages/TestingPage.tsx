import { useEffect, useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { FlaskConical, Send, RotateCcw, History } from "lucide-react";
import { Card } from "../components/Card";
import { EvidencePanel } from "../components/EvidencePanel";
import { ConfidenceBadge } from "../components/agents/ConfidenceBadge";
import { RunProgress } from "../components/agents/RunProgress";
import { RunStatusBadge } from "../components/agents/RunStatusBadge";
import { RunDetailsAccordion } from "../components/agents/RunDetailsAccordion";
import { TestingResultDetails } from "../components/agents/StageResultDetails";
import {
  PlanningRunPicker,
  StandaloneContextBanner,
} from "../components/agents/StandalonePlanningContext";
import { useAgentRun } from "../hooks/useAgentRun";
import type { TestPlanResult } from "../types/agent";

const EXAMPLES = [
  "Test strategy for JWT authentication across all services",
  "Test plan for splitting OrderService into CQRS",
  "Validate Kafka schema change in order.created event",
  "Regression plan for adding Redis caching to payment-service",
];

export function TestingPage() {
  const [input, setInput] = useState("");
  const [planningRunId, setPlanningRunId] = useState<string | null>(null);
  const [searchParams] = useSearchParams();
  const { run, isSubmitting, error, submit, reset } = useAgentRun();

  // Deep-linked from a conversational turn's "Validate migration" action
  // — prefills rather than auto-submits, same as Planning's own prefill.
  useEffect(() => {
    const prefill = searchParams.get("prefill");
    if (prefill) setInput(prefill);
  }, [searchParams]);

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    const trimmed = input.trim();
    if (!trimmed) return;
    submit({
      subject_reference: trimmed,
      goal: "plan_tests",
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
          <div className="rounded-lg bg-cat-5-bg p-2 ring-1 ring-inset ring-cat-5-line/30">
            <FlaskConical className="h-5 w-5 text-cat-5-fg" aria-hidden="true" />
          </div>
          <div>
            <h1 className="text-xl font-semibold text-fg">Test Planning Agent</h1>
            <p className="text-sm text-fg-muted">
              Describe an engineering change. GraphForge produces a structured testing strategy —
              regression scope, integration tests, edge cases, execution order — grounded in your
              architecture graph.
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
                htmlFor="testing-input"
                className="block text-sm font-medium text-fg-secondary"
              >
                What change needs testing?
              </label>
              <textarea
                id="testing-input"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                disabled={isSubmitting}
                placeholder="Describe the engineering change that needs a test strategy…"
                rows={4}
                className="mt-2 w-full rounded-lg border border-line bg-surface-raised px-4 py-3 text-sm text-fg placeholder-fg-subtle focus:border-cat-5-line disabled:opacity-50"
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
                      className="rounded-md border border-line px-2.5 py-1 text-xs text-fg-muted transition-colors hover:border-cat-5-line/40 hover:text-cat-5-fg"
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
                aria-label="Submit testing request"
              >
                <Send className="h-4 w-4" aria-hidden="true" />
                {isSubmitting ? "Analyzing…" : "Generate Test Plan"}
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
        <TestPlanResultView
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

function TestPlanResultView({
  run,
  onNewPlan,
  groundedInPlanningRunId,
}: {
  run: NonNullable<ReturnType<typeof useAgentRun>["run"]>;
  onNewPlan: () => void;
  groundedInPlanningRunId: string | null;
}) {
  const step = run.steps[0];
  const result = step?.result as unknown as TestPlanResult | undefined;
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
          New Test Plan
        </button>
      </div>

      {/* Error */}
      {run.status === "failed" && run.error_message && (
        <div className="rounded-lg border border-danger-line/30 bg-danger-bg px-4 py-3 text-sm text-danger-fg">
          <strong>Error:</strong> {run.error_message}
        </div>
      )}

      {/* The validation story leads — "how do we know this is safe" comes
          before run metadata, matching Planning/Development. */}
      {result && <TestingResultDetails result={result} />}

      {/* Evidence */}
      <EvidencePanel evidence={evidence} />

      {/* Run metadata (collapsible) */}
      <RunDetailsAccordion run={run} step={step} goalLabel="Test Planning" />
    </div>
  );
}
