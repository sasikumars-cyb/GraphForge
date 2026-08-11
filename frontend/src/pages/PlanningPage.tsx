import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { Lightbulb, Send, RotateCcw, History } from "lucide-react";
import { Card } from "../components/Card";
import { EvidencePanel } from "../components/EvidencePanel";
import { RunProgress } from "../components/agents/RunProgress";
import { RunStatusBadge } from "../components/agents/RunStatusBadge";
import { RunDetailsAccordion } from "../components/agents/RunDetailsAccordion";
import { PlanningResultDetails } from "../components/agents/StageResultDetails";
import { BlueprintExplorer } from "../components/blueprint/BlueprintExplorer";
import { KnowledgeSourcesPanel } from "../components/planning/KnowledgeSourcesPanel";
import { PlanningConfidencePanel } from "../components/planning/PlanningConfidencePanel";
import {
  GreenfieldBanner,
  GreenfieldRecommendations,
} from "../components/planning/GreenfieldRecommendations";
import { useAgentRun } from "../hooks/useAgentRun";
import type { PlanningResult } from "../types/agent";
import type { BlueprintArtifact } from "../types/blueprint";

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
          <div className="rounded-lg bg-info-bg p-2 ring-1 ring-inset ring-info-line/30">
            <Lightbulb className="h-5 w-5 text-info-fg" aria-hidden="true" />
          </div>
          <div>
            <h1 className="text-xl font-semibold text-fg">Planning Assistant</h1>
            <p className="text-sm text-fg-muted">
              Describe an engineering task. GraphForge queries your architecture graph and produces
              a plan backed by verifiable evidence — not hallucination.
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

      {/* Input form */}
      {!hasResult && (
        <Card>
          <form onSubmit={handleSubmit} className="flex flex-col gap-4">
            <div>
              <label htmlFor="planning-input" className="block text-sm font-medium text-fg-secondary">
                What would you like to plan?
              </label>
              <textarea
                id="planning-input"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                disabled={isSubmitting}
                placeholder="Describe your engineering task, feature, or refactoring goal…"
                rows={4}
                className="mt-2 w-full rounded-lg border border-line bg-surface-raised px-4 py-3 text-sm text-fg placeholder-fg-subtle focus:border-info-line disabled:opacity-50"
                aria-required="true"
              />
            </div>

            {!isSubmitting && !run && (
              <div>
                <p className="mb-2 text-xs font-medium text-fg-muted">Try an example:</p>
                <div className="flex flex-wrap gap-2">
                  {EXAMPLES.map((example) => (
                    <button
                      key={example}
                      type="button"
                      onClick={() => setInput(example)}
                      className="rounded-md border border-line px-2.5 py-1 text-xs text-fg-muted transition-colors hover:border-info-line/40 hover:text-info-fg"
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
                className="inline-flex items-center gap-2 rounded-lg bg-info-solid px-4 py-2 text-sm font-medium text-info-on-solid transition-colors hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50"
              >
                <Send className="h-4 w-4" aria-hidden="true" />
                {isSubmitting ? "Planning…" : "Generate Plan"}
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
      {hasResult && <PlanningResultView run={run} onNewPlan={handleNewPlan} />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Result view
// ---------------------------------------------------------------------------

function PlanningResultView({
  run,
  onNewPlan,
}: {
  run: NonNullable<ReturnType<typeof useAgentRun>["run"]>;
  onNewPlan: () => void;
}) {
  const step = run.steps[0];
  // AgentStep.result is a JSON column defaulting to `{}` and is only ever
  // populated on the success path (see RunCoordinator.execute_run) — a
  // failed run's step keeps that empty default forever, not `null`. `{}`
  // is truthy, so treating step.result as the result whenever it's merely
  // *present* let every result-shaped section below render against an
  // empty object once a run failed (e.g. expired Bedrock/AWS credentials
  // mid-plan) — GreenfieldRecommendations crashed hardest since it calls
  // `.map()` on `affected_components` with no guard, but every other
  // result-consuming section here was one non-optional field away from
  // the same failure. Requiring "completed" is what actually distinguishes
  // a real result from that empty default; the "failed" banner below
  // already covers user-facing error communication for every other status.
  const result =
    run.status === "completed"
      ? (step?.result as unknown as PlanningResult | undefined)
      : undefined;
  const evidence = step?.evidence ?? [];
  const blueprint = result?.blueprint as BlueprintArtifact | null | undefined;
  const hasBlueprint = Boolean(blueprint && blueprint.diagrams.length > 0);

  const isGreenfield =
    !(result?.graph_context_used ?? false) &&
    (result?.repositories_consulted?.length ?? 0) === 0;

  return (
    <div className="flex flex-col gap-6">
      {/* ── Header ─────────────────────────────────────────────────────────── */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <RunStatusBadge status={run.status} />
          {run.subject.display_name && (
            <span
              className="max-w-md truncate text-sm text-fg-muted"
              title={run.subject.display_name}
            >
              {run.subject.display_name}
            </span>
          )}
        </div>
        <button
          type="button"
          onClick={onNewPlan}
          className="inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium text-fg-muted ring-1 ring-inset ring-line transition-colors hover:bg-surface-raised hover:text-fg-secondary"
        >
          <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />
          New Plan
        </button>
      </div>

      {/* ── Greenfield notice ───────────────────────────────────────────────── */}
      {isGreenfield && result && <GreenfieldBanner result={result} />}

      {/* ── Error ───────────────────────────────────────────────────────────── */}
      {run.status === "failed" && run.error_message && (
        <div className="rounded-lg border border-danger-line/30 bg-danger-bg px-4 py-3 text-sm text-danger-fg">
          <strong>Error:</strong> {run.error_message}
        </div>
      )}

      {/* ── Blueprint hero ───────────────────────────────────────────────────
          Primary output: full-width, all sections expanded by default.
          Always rendered when the blueprint exists — even greenfield projects
          produce architecture and data flow diagrams synthesized from the brief.
          Invalid diagrams are replaced by informative empty states inside
          BlueprintExplorer via the diagram validator. */}
      {hasBlueprint && blueprint && (
        <Card
          title="Visual Blueprint"
          description={`${blueprint.diagrams.length} diagram${blueprint.diagrams.length === 1 ? "" : "s"} · synthesized from the engineering brief`}
        >
          <BlueprintExplorer blueprint={blueprint} defaultExpanded />
        </Card>
      )}

      {!hasBlueprint && (
        <Card title="Visual Blueprint">
          <div className="flex flex-col items-center justify-center gap-2 py-12 text-center">
            <p className="text-sm font-medium text-fg-muted">No visual blueprint was generated.</p>
            <p className="max-w-sm text-xs text-fg-muted">
              The planning agent produced a text plan. Visual diagrams are generated when the LLM
              returns structured architecture, data flow, or entity data.
            </p>
          </div>
        </Card>
      )}

      {/* ── Main content + sidebar ──────────────────────────────────────────── */}
      <div className="grid grid-cols-1 gap-6 xl:grid-cols-[1fr_300px]">
        {/* Left: implementation details */}
        <div className="min-w-0">
          {result && <PlanningResultDetails result={result} />}
        </div>

        {/* Right sidebar: knowledge sources + confidence */}
        <div className="flex flex-col gap-4">
          <KnowledgeSourcesPanel result={result} evidence={evidence} />
          {step?.confidence && (
            <PlanningConfidencePanel confidence={step.confidence} result={result} />
          )}
        </div>
      </div>

      {/* ── Greenfield recommendations ──────────────────────────────────────── */}
      {isGreenfield && result && <GreenfieldRecommendations result={result} />}

      {/* ── Evidence trail (collapsed by default) ──────────────────────────── */}
      <EvidencePanel evidence={evidence} />

      {/* ── Run metadata (collapsible) ──────────────────────────────────────── */}
      <RunDetailsAccordion run={run} step={step} />
    </div>
  );
}
