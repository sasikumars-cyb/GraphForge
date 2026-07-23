import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { Code2, Send, RotateCcw, History, GitBranch, AlertTriangle, Recycle, Layers } from "lucide-react";
import { Card } from "../components/Card";
import { EvidencePanel } from "../components/EvidencePanel";
import { ConfidenceBadge } from "../components/agents/ConfidenceBadge";
import { RunProgress } from "../components/agents/RunProgress";
import { RunStatusBadge } from "../components/agents/RunStatusBadge";
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
  const { run, isSubmitting, error, submit, reset } = useAgentRun();

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    const trimmed = input.trim();
    if (!trimmed) return;
    submit({ subject_reference: trimmed, goal: "develop_change_plan" });
  };

  const handleExampleClick = (example: string) => {
    setInput(example);
  };

  const handleNewPlan = () => {
    reset();
    setInput("");
  };

  const hasResult = run && (run.status === "completed" || run.status === "partial" || run.status === "failed");

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="rounded-lg bg-violet-500/10 p-2 ring-1 ring-inset ring-violet-500/30">
            <Code2 className="h-5 w-5 text-violet-400" aria-hidden="true" />
          </div>
          <div>
            <h2 className="text-xl font-semibold text-slate-50">Development Agent</h2>
            <p className="text-sm text-slate-400">
              Describe an engineering change. GraphForge produces a structured implementation blueprint — repositories, components, phases, risks — grounded in your architecture graph.
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
              <label htmlFor="development-input" className="block text-sm font-medium text-slate-200">
                What would you like to implement?
              </label>
              <textarea
                id="development-input"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                disabled={isSubmitting}
                placeholder="Describe your engineering change, feature, or refactoring goal…"
                rows={4}
                className="mt-2 w-full rounded-lg border border-slate-700 bg-slate-800/60 px-4 py-3 text-sm text-slate-100 placeholder-slate-500 focus:border-violet-500 focus:outline-none focus:ring-1 focus:ring-violet-500 disabled:opacity-50"
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
                      className="rounded-md border border-slate-700 px-2.5 py-1 text-xs text-slate-400 transition-colors hover:border-violet-500/40 hover:text-violet-300"
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
                className="inline-flex items-center gap-2 rounded-lg bg-violet-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-violet-500 disabled:cursor-not-allowed disabled:opacity-50"
                aria-label="Submit development request"
              >
                <Send className="h-4 w-4" aria-hidden="true" />
                {isSubmitting ? "Analyzing…" : "Generate Blueprint"}
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
      {hasResult && <DevelopmentResultView run={run} onNewPlan={handleNewPlan} />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Result sub-component
// ---------------------------------------------------------------------------

function DevelopmentResultView({ run, onNewPlan }: { run: NonNullable<ReturnType<typeof useAgentRun>["run"]>; onNewPlan: () => void }) {
  const step = run.steps[0];
  const result = step?.result as unknown as DevelopmentPlanResult | undefined;
  const evidence = step?.evidence ?? [];

  return (
    <div className="flex flex-col gap-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <RunStatusBadge status={run.status} />
          {step?.confidence && (
            <ConfidenceBadge confidence={step.confidence} showReasoning />
          )}
        </div>
        <button
          type="button"
          onClick={onNewPlan}
          className="inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium text-slate-400 ring-1 ring-inset ring-slate-700 transition-colors hover:bg-slate-800 hover:text-slate-200"
        >
          <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />
          New Blueprint
        </button>
      </div>

      {/* Run metadata */}
      <Card title="Run Details">
        <dl className="grid grid-cols-2 gap-x-6 gap-y-3 text-sm sm:grid-cols-3 lg:grid-cols-4">
          <div>
            <dt className="text-xs text-slate-500">Goal</dt>
            <dd className="text-slate-200">Change Plan</dd>
          </div>
          <div>
            <dt className="text-xs text-slate-500">Subject</dt>
            <dd className="truncate text-slate-200" title={run.subject.display_name}>
              {run.subject.display_name}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-slate-500">Status</dt>
            <dd><RunStatusBadge status={run.status} /></dd>
          </div>
          <div>
            <dt className="text-xs text-slate-500">Confidence</dt>
            <dd>
              {step?.confidence ? (
                <ConfidenceBadge confidence={step.confidence} />
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

      {/* Error */}
      {run.status === "failed" && run.error_message && (
        <div className="rounded-lg border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-300">
          <strong>Error:</strong> {run.error_message}
        </div>
      )}

      {/* Development Plan Result */}
      {result && (
        <>
          {/* Executive Summary */}
          {result.executive_summary && (
            <Card title="Implementation Blueprint">
              <p className="text-sm text-slate-200">{result.executive_summary}</p>
            </Card>
          )}

          {/* Implementation Phases */}
          {result.implementation_phases && result.implementation_phases.length > 0 && (
            <Card
              title="Implementation Phases"
              description={`${result.implementation_phases.length} phase${result.implementation_phases.length === 1 ? "" : "s"}`}
            >
              <ol className="space-y-3" role="list">
                {result.implementation_phases.map((phase, i) => (
                  <li
                    key={i}
                    className="flex gap-3 rounded-lg border border-slate-800 bg-slate-900/40 px-4 py-3"
                  >
                    <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-violet-500/10 text-xs font-semibold text-violet-300 ring-1 ring-inset ring-violet-500/30">
                      {phase.order ?? i + 1}
                    </span>
                    <div className="min-w-0 flex-1">
                      <p className="text-sm font-medium text-slate-200">{phase.title}</p>
                      <p className="mt-1 text-sm text-slate-400">{phase.description}</p>
                      <div className="mt-2 flex flex-wrap items-center gap-2">
                        {phase.estimated_complexity && (
                          <span className={`rounded px-1.5 py-0.5 text-xs font-medium ${
                            phase.estimated_complexity === "high" ? "bg-rose-500/10 text-rose-300" :
                            phase.estimated_complexity === "medium" ? "bg-amber-500/10 text-amber-300" :
                            "bg-emerald-500/10 text-emerald-300"
                          }`}>
                            {phase.estimated_complexity}
                          </span>
                        )}
                        {phase.affected_components.map((comp) => (
                          <span key={comp} className="rounded bg-slate-800 px-1.5 py-0.5 text-xs text-slate-400">
                            {comp}
                          </span>
                        ))}
                        {phase.depends_on_phases.length > 0 && (
                          <span className="text-xs text-slate-500">
                            depends on: {phase.depends_on_phases.join(", ")}
                          </span>
                        )}
                      </div>
                    </div>
                  </li>
                ))}
              </ol>
            </Card>
          )}

          {/* Repositories and Components - side by side */}
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            {/* Affected Repositories */}
            {result.repositories && result.repositories.length > 0 && (
              <Card title="Affected Repositories" description={`${result.repositories.length} repo${result.repositories.length === 1 ? "" : "s"}`}>
                <ul className="space-y-2" role="list">
                  {result.repositories.map((repo, i) => (
                    <li key={i} className="flex items-start gap-2 rounded-md border border-slate-800 bg-slate-900/30 px-3 py-2">
                      <GitBranch className="mt-0.5 h-4 w-4 shrink-0 text-violet-400" aria-hidden="true" />
                      <div className="min-w-0 flex-1">
                        <p className="text-sm font-medium text-slate-200">{repo.name}</p>
                        {repo.reason && <p className="text-xs text-slate-400">{repo.reason}</p>}
                      </div>
                    </li>
                  ))}
                </ul>
              </Card>
            )}

            {/* Affected Components */}
            {result.components && result.components.length > 0 && (
              <Card title="Affected Components" description={`${result.components.length} component${result.components.length === 1 ? "" : "s"}`}>
                <ul className="space-y-2" role="list">
                  {result.components.map((comp, i) => (
                    <li key={i} className="flex items-start gap-2 rounded-md border border-slate-800 bg-slate-900/30 px-3 py-2">
                      <Layers className="mt-0.5 h-4 w-4 shrink-0 text-sky-400" aria-hidden="true" />
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2">
                          <p className="text-sm font-medium text-slate-200">{comp.name}</p>
                          {comp.component_type && (
                            <span className="rounded bg-slate-800 px-1.5 py-0.5 text-xs text-slate-400">{comp.component_type}</span>
                          )}
                        </div>
                        {comp.change_description && <p className="mt-0.5 text-xs text-slate-400">{comp.change_description}</p>}
                        {comp.repository && <p className="text-xs text-slate-500">{comp.repository}{comp.file_path ? ` • ${comp.file_path}` : ""}</p>}
                      </div>
                    </li>
                  ))}
                </ul>
              </Card>
            )}
          </div>

          {/* Dependencies */}
          {result.dependencies && result.dependencies.length > 0 && (
            <Card title="Dependencies" description={`${result.dependencies.length} relationship${result.dependencies.length === 1 ? "" : "s"}`}>
              <ul className="space-y-2" role="list">
                {result.dependencies.map((dep, i) => (
                  <li key={i} className="flex items-center gap-2 rounded-md border border-slate-800 bg-slate-900/30 px-3 py-2 text-sm">
                    <span className="text-slate-200">{dep.source}</span>
                    <span className="rounded bg-sky-500/10 px-1.5 py-0.5 text-xs font-medium text-sky-300">{dep.relationship}</span>
                    <span className="text-slate-200">{dep.target}</span>
                    {dep.risk_note && (
                      <span className="ml-auto text-xs text-amber-400">{dep.risk_note}</span>
                    )}
                  </li>
                ))}
              </ul>
            </Card>
          )}

          {/* Reusable Implementations */}
          {result.reusable_implementations && result.reusable_implementations.length > 0 && (
            <Card title="Reuse Candidates" description="Existing implementations to leverage">
              <ul className="space-y-2" role="list">
                {result.reusable_implementations.map((impl, i) => (
                  <li key={i} className="flex items-start gap-2 rounded-md border border-emerald-500/20 bg-emerald-500/5 px-3 py-2">
                    <Recycle className="mt-0.5 h-4 w-4 shrink-0 text-emerald-400" aria-hidden="true" />
                    <div className="min-w-0 flex-1">
                      <p className="text-sm font-medium text-emerald-200">{impl.name}</p>
                      {impl.reason && <p className="text-xs text-slate-400">{impl.reason}</p>}
                      {impl.repository && <p className="text-xs text-slate-500">in {impl.repository}</p>}
                    </div>
                  </li>
                ))}
              </ul>
            </Card>
          )}

          {/* Risks */}
          {result.risks && result.risks.length > 0 && (
            <Card title="Risks" description={`${result.risks.length} identified risk${result.risks.length === 1 ? "" : "s"}`}>
              <ul className="space-y-2" role="list">
                {result.risks.map((risk, i) => (
                  <li key={i} className="flex items-start gap-2 rounded-md border border-amber-500/20 bg-amber-500/5 px-3 py-2">
                    <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-400" aria-hidden="true" />
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <p className="text-sm text-slate-200">{risk.description}</p>
                        {risk.severity && (
                          <span className={`shrink-0 rounded px-1.5 py-0.5 text-xs font-medium ${
                            risk.severity === "critical" ? "bg-rose-500/10 text-rose-300" :
                            risk.severity === "high" ? "bg-rose-500/10 text-rose-300" :
                            risk.severity === "medium" ? "bg-amber-500/10 text-amber-300" :
                            "bg-slate-800 text-slate-400"
                          }`}>
                            {risk.severity}
                          </span>
                        )}
                      </div>
                      {risk.mitigation && <p className="mt-1 text-xs text-slate-400">Mitigation: {risk.mitigation}</p>}
                      {risk.affected_component && <p className="text-xs text-slate-500">Affects: {risk.affected_component}</p>}
                    </div>
                  </li>
                ))}
              </ul>
            </Card>
          )}

          {/* Recommendations */}
          {result.recommendations && result.recommendations.length > 0 && (
            <Card title="Recommendations">
              <ul className="space-y-2">
                {result.recommendations.map((rec, i) => (
                  <li key={i} className="flex items-start gap-2 text-sm text-slate-300">
                    <span className="mt-1 h-1.5 w-1.5 shrink-0 rounded-full bg-violet-400" aria-hidden="true" />
                    {rec}
                  </li>
                ))}
              </ul>
            </Card>
          )}

          {/* Graph context indicator */}
          <div className="text-xs text-slate-500">
            Graph context: {result.graph_context_used ? "Blueprint grounded in architecture graph data" : "No graph data available — general engineering practices used"}
          </div>
        </>
      )}

      {/* Evidence */}
      <EvidencePanel evidence={evidence} />
    </div>
  );
}
