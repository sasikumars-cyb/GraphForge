import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { Lightbulb, Send, RotateCcw, History } from "lucide-react";
import { Card } from "../components/Card";
import { EvidencePanel } from "../components/EvidencePanel";
import { ConfidenceBadge } from "../components/agents/ConfidenceBadge";
import { RunProgress } from "../components/agents/RunProgress";
import { RunStatusBadge } from "../components/agents/RunStatusBadge";
import { PlanningResultDetails } from "../components/agents/StageResultDetails";
import { useAgentRun } from "../hooks/useAgentRun";
import type { PlanningResult } from "../types/agent";

const EXAMPLES = [
  "Plan migration from Kafka to Google PubSub",
  "Implement a new payment retry feature for order-service",
  "Refactor billing service to use event-driven architecture",
  "Add health check endpoints across all microservices",
];

export function PlanningPage() {
  const [input, setInput] = useState("");
  const { run, isSubmitting, error, submit, reset } = useAgentRun();

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    const trimmed = input.trim();
    if (!trimmed) return;
    submit({ subject_reference: trimmed, goal: "plan_freeform" });
  };

  const handleExampleClick = (example: string) => {
    setInput(example);
  };

  const handleNewPlan = () => {
    reset();
    setInput("");
  };

  const hasResult =
    run && (run.status === "completed" || run.status === "partial" || run.status === "failed");

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="rounded-lg bg-sky-500/10 p-2 ring-1 ring-inset ring-sky-500/30">
            <Lightbulb className="h-5 w-5 text-sky-400" aria-hidden="true" />
          </div>
          <div>
            <h2 className="text-xl font-semibold text-slate-50">Planning Assistant</h2>
            <p className="text-sm text-slate-400">
              Describe an engineering task. GraphForge queries your architecture graph and produces
              a plan backed by verifiable evidence — not hallucination.
            </p>
          </div>
        </div>
        <Link
          to="/runs"
          className="flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium text-slate-400 ring-1 ring-inset ring-slate-700 transition-colors hover:bg-slate-800 hover:text-slate-200"
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
            <div>
              <label htmlFor="planning-input" className="block text-sm font-medium text-slate-200">
                What would you like to plan?
              </label>
              <textarea
                id="planning-input"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                disabled={isSubmitting}
                placeholder="Describe your engineering task, feature, or refactoring goal…"
                rows={4}
                className="mt-2 w-full rounded-lg border border-slate-700 bg-slate-800/60 px-4 py-3 text-sm text-slate-100 placeholder-slate-500 focus:border-sky-500 focus:outline-none focus:ring-1 focus:ring-sky-500 disabled:opacity-50"
                aria-required="true"
              />
            </div>

            {/* Examples */}
            {!isSubmitting && !run && (
              <div>
                <p className="mb-2 text-xs font-medium text-slate-500">Try an example:</p>
                <div className="flex flex-wrap gap-2">
                  {EXAMPLES.map((example) => (
                    <button
                      key={example}
                      type="button"
                      onClick={() => handleExampleClick(example)}
                      className="rounded-md border border-slate-700 px-2.5 py-1 text-xs text-slate-400 transition-colors hover:border-sky-500/40 hover:text-sky-300"
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
                className="inline-flex items-center gap-2 rounded-lg bg-sky-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-sky-500 disabled:cursor-not-allowed disabled:opacity-50"
                aria-label="Submit planning request"
              >
                <Send className="h-4 w-4" aria-hidden="true" />
                {isSubmitting ? "Planning…" : "Generate Plan"}
              </button>
              {isSubmitting && (
                <span className="text-xs text-slate-500">This may take up to a minute.</span>
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
        <div className="rounded-lg border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-300">
          {error}
          <button
            type="button"
            onClick={handleNewPlan}
            className="ml-3 text-rose-200 underline hover:text-rose-100"
          >
            Try again
          </button>
        </div>
      )}

      {/* Result */}
      {hasResult && <PlanningResultView run={run} onNewPlan={handleNewPlan} />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Result sub-component
// ---------------------------------------------------------------------------

function PlanningResultView({
  run,
  onNewPlan,
}: {
  run: NonNullable<ReturnType<typeof useAgentRun>["run"]>;
  onNewPlan: () => void;
}) {
  const step = run.steps[0];
  const result = step?.result as unknown as PlanningResult | undefined;
  const evidence = step?.evidence ?? [];

  return (
    <div className="flex flex-col gap-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <RunStatusBadge status={run.status} />
          {step?.confidence && <ConfidenceBadge confidence={step.confidence} showReasoning />}
        </div>
        <button
          type="button"
          onClick={onNewPlan}
          className="inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium text-slate-400 ring-1 ring-inset ring-slate-700 transition-colors hover:bg-slate-800 hover:text-slate-200"
        >
          <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />
          New Plan
        </button>
      </div>

      {/* Run metadata */}
      <Card title="Run Details">
        <dl className="grid grid-cols-2 gap-x-6 gap-y-3 text-sm sm:grid-cols-3 lg:grid-cols-4">
          <div>
            <dt className="text-xs text-slate-500">Goal</dt>
            <dd className="text-slate-200">{run.goal}</dd>
          </div>
          <div>
            <dt className="text-xs text-slate-500">Subject</dt>
            <dd className="truncate text-slate-200" title={run.subject.display_name}>
              {run.subject.display_name}
            </dd>
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
        </dl>
      </Card>

      {/* Error message */}
      {run.status === "failed" && run.error_message && (
        <div className="rounded-lg border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-300">
          <strong>Error:</strong> {run.error_message}
        </div>
      )}

      {/* Planning result */}
      {result && <PlanningResultDetails result={result} />}

      {/* Evidence */}
      <EvidencePanel evidence={evidence} />
    </div>
  );
}
