import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { FlaskConical, Send, RotateCcw, History, AlertTriangle, CheckCircle2, Zap, Users } from "lucide-react";
import { Card } from "../components/Card";
import { EvidencePanel } from "../components/EvidencePanel";
import { ConfidenceBadge } from "../components/agents/ConfidenceBadge";
import { RunProgress } from "../components/agents/RunProgress";
import { RunStatusBadge } from "../components/agents/RunStatusBadge";
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
  const { run, isSubmitting, error, submit, reset } = useAgentRun();

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    const trimmed = input.trim();
    if (!trimmed) return;
    submit({ subject_reference: trimmed, goal: "plan_tests" });
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
          <div className="rounded-lg bg-emerald-500/10 p-2 ring-1 ring-inset ring-emerald-500/30">
            <FlaskConical className="h-5 w-5 text-emerald-400" aria-hidden="true" />
          </div>
          <div>
            <h2 className="text-xl font-semibold text-slate-50">Test Planning Agent</h2>
            <p className="text-sm text-slate-400">
              Describe an engineering change. GraphForge produces a structured testing strategy — regression scope, integration tests, edge cases, execution order — grounded in your architecture graph.
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
              <label htmlFor="testing-input" className="block text-sm font-medium text-slate-200">
                What change needs testing?
              </label>
              <textarea
                id="testing-input"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                disabled={isSubmitting}
                placeholder="Describe the engineering change that needs a test strategy…"
                rows={4}
                className="mt-2 w-full rounded-lg border border-slate-700 bg-slate-800/60 px-4 py-3 text-sm text-slate-100 placeholder-slate-500 focus:border-emerald-500 focus:outline-none focus:ring-1 focus:ring-emerald-500 disabled:opacity-50"
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
                      className="rounded-md border border-slate-700 px-2.5 py-1 text-xs text-slate-400 transition-colors hover:border-emerald-500/40 hover:text-emerald-300"
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
                className="inline-flex items-center gap-2 rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-emerald-500 disabled:cursor-not-allowed disabled:opacity-50"
                aria-label="Submit testing request"
              >
                <Send className="h-4 w-4" aria-hidden="true" />
                {isSubmitting ? "Analyzing…" : "Generate Test Plan"}
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
      {hasResult && <TestPlanResultView run={run} onNewPlan={handleNewPlan} />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Result sub-component
// ---------------------------------------------------------------------------

function TestPlanResultView({ run, onNewPlan }: { run: NonNullable<ReturnType<typeof useAgentRun>["run"]>; onNewPlan: () => void }) {
  const step = run.steps[0];
  const result = step?.result as unknown as TestPlanResult | undefined;
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
          New Test Plan
        </button>
      </div>

      {/* Run metadata */}
      <Card title="Run Details">
        <dl className="grid grid-cols-2 gap-x-6 gap-y-3 text-sm sm:grid-cols-3 lg:grid-cols-4">
          <div>
            <dt className="text-xs text-slate-500">Goal</dt>
            <dd className="text-slate-200">Test Planning</dd>
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

      {/* Test Plan Result */}
      {result && (
        <>
          {/* Executive Summary */}
          {result.executive_summary && (
            <Card title="Test Strategy">
              <p className="text-sm text-slate-200">{result.executive_summary}</p>
            </Card>
          )}

          {/* Test Scope */}
          {result.test_scope && (result.test_scope.in_scope.length > 0 || result.test_scope.out_of_scope.length > 0) && (
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              {result.test_scope.in_scope.length > 0 && (
                <Card title="In Scope">
                  <ul className="space-y-1">
                    {result.test_scope.in_scope.map((item, i) => (
                      <li key={i} className="flex items-start gap-2 text-sm text-emerald-200">
                        <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-400" aria-hidden="true" />
                        {item}
                      </li>
                    ))}
                  </ul>
                </Card>
              )}
              {result.test_scope.out_of_scope.length > 0 && (
                <Card title="Out of Scope">
                  <ul className="space-y-1">
                    {result.test_scope.out_of_scope.map((item, i) => (
                      <li key={i} className="text-sm text-slate-400">{item}</li>
                    ))}
                  </ul>
                </Card>
              )}
            </div>
          )}

          {/* Execution Order */}
          {result.execution_order && result.execution_order.length > 0 && (
            <Card
              title="Execution Order"
              description={`${result.execution_order.length} phase${result.execution_order.length === 1 ? "" : "s"}`}
            >
              <ol className="space-y-3" role="list">
                {result.execution_order.map((phase, i) => (
                  <li
                    key={i}
                    className="flex gap-3 rounded-lg border border-slate-800 bg-slate-900/40 px-4 py-3"
                  >
                    <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-emerald-500/10 text-xs font-semibold text-emerald-300 ring-1 ring-inset ring-emerald-500/30">
                      {phase.order ?? i + 1}
                    </span>
                    <div className="min-w-0 flex-1">
                      <p className="text-sm font-medium text-slate-200">{phase.title}</p>
                      <p className="mt-1 text-sm text-slate-400">{phase.description}</p>
                      <div className="mt-2 flex flex-wrap gap-2">
                        {phase.test_types.map((type) => (
                          <span key={type} className="rounded bg-emerald-500/10 px-1.5 py-0.5 text-xs text-emerald-300">
                            {type}
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

          {/* Regression + Integration Tests */}
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            {result.regression_tests && result.regression_tests.length > 0 && (
              <Card title="Regression Tests" description={`${result.regression_tests.length} test${result.regression_tests.length === 1 ? "" : "s"}`}>
                <ul className="space-y-2" role="list">
                  {result.regression_tests.map((test, i) => (
                    <li key={i} className="rounded-md border border-slate-800 bg-slate-900/30 px-3 py-2">
                      <div className="flex items-center gap-2">
                        <p className="flex-1 text-sm text-slate-200">{test.description}</p>
                        {test.priority && (
                          <span className={`shrink-0 rounded px-1.5 py-0.5 text-xs font-medium ${
                            test.priority === "critical" ? "bg-rose-500/10 text-rose-300" :
                            test.priority === "high" ? "bg-amber-500/10 text-amber-300" :
                            test.priority === "medium" ? "bg-sky-500/10 text-sky-300" :
                            "bg-slate-800 text-slate-400"
                          }`}>
                            {test.priority}
                          </span>
                        )}
                      </div>
                      <div className="mt-1 flex items-center gap-2 text-xs text-slate-500">
                        <span>{test.component}</span>
                        {test.automated && <span className="text-emerald-400">automated</span>}
                      </div>
                    </li>
                  ))}
                </ul>
              </Card>
            )}

            {result.integration_tests && result.integration_tests.length > 0 && (
              <Card title="Integration Tests" description={`${result.integration_tests.length} test${result.integration_tests.length === 1 ? "" : "s"}`}>
                <ul className="space-y-2" role="list">
                  {result.integration_tests.map((test, i) => (
                    <li key={i} className="rounded-md border border-slate-800 bg-slate-900/30 px-3 py-2">
                      <p className="text-sm text-slate-200">{test.description}</p>
                      <div className="mt-1 flex items-center gap-2 text-xs text-slate-500">
                        <span>{test.source_component}</span>
                        <span className="rounded bg-sky-500/10 px-1 text-sky-300">{test.relationship}</span>
                        <span>{test.target_component}</span>
                        {test.priority && (
                          <span className={`ml-auto rounded px-1.5 py-0.5 text-xs font-medium ${
                            test.priority === "critical" ? "bg-rose-500/10 text-rose-300" :
                            test.priority === "high" ? "bg-amber-500/10 text-amber-300" :
                            "bg-slate-800 text-slate-400"
                          }`}>
                            {test.priority}
                          </span>
                        )}
                      </div>
                    </li>
                  ))}
                </ul>
              </Card>
            )}
          </div>

          {/* Edge Cases */}
          {result.edge_cases && result.edge_cases.length > 0 && (
            <Card title="Edge Cases & Negative Scenarios" description={`${result.edge_cases.length} case${result.edge_cases.length === 1 ? "" : "s"}`}>
              <ul className="space-y-2" role="list">
                {result.edge_cases.map((edge, i) => (
                  <li key={i} className="flex items-start gap-2 rounded-md border border-amber-500/20 bg-amber-500/5 px-3 py-2">
                    <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-400" aria-hidden="true" />
                    <div className="min-w-0 flex-1">
                      <p className="text-sm text-slate-200">{edge.description}</p>
                      <div className="mt-1 flex items-center gap-2 text-xs text-slate-500">
                        {edge.component && <span>{edge.component}</span>}
                        {edge.category && <span className="rounded bg-slate-800 px-1 text-slate-400">{edge.category}</span>}
                        {edge.severity && (
                          <span className={`rounded px-1.5 py-0.5 font-medium ${
                            edge.severity === "critical" || edge.severity === "high" ? "bg-rose-500/10 text-rose-300" :
                            "bg-amber-500/10 text-amber-300"
                          }`}>
                            {edge.severity}
                          </span>
                        )}
                      </div>
                    </div>
                  </li>
                ))}
              </ul>
            </Card>
          )}

          {/* Automation + Manual */}
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            {result.automation_candidates && result.automation_candidates.length > 0 && (
              <Card title="Automation Candidates" description="Tests to automate">
                <ul className="space-y-2" role="list">
                  {result.automation_candidates.map((auto, i) => (
                    <li key={i} className="flex items-start gap-2 rounded-md border border-emerald-500/20 bg-emerald-500/5 px-3 py-2">
                      <Zap className="mt-0.5 h-4 w-4 shrink-0 text-emerald-400" aria-hidden="true" />
                      <div className="min-w-0 flex-1">
                        <p className="text-sm text-emerald-200">{auto.description}</p>
                        <div className="mt-1 flex items-center gap-2 text-xs text-slate-500">
                          {auto.component && <span>{auto.component}</span>}
                          {auto.test_type && <span className="rounded bg-emerald-500/10 px-1 text-emerald-300">{auto.test_type}</span>}
                        </div>
                        {auto.reason && <p className="mt-1 text-xs text-slate-400">{auto.reason}</p>}
                      </div>
                    </li>
                  ))}
                </ul>
              </Card>
            )}

            {result.manual_validations && result.manual_validations.length > 0 && (
              <Card title="Manual Validation" description="Requires human review">
                <ul className="space-y-2" role="list">
                  {result.manual_validations.map((manual, i) => (
                    <li key={i} className="flex items-start gap-2 rounded-md border border-slate-700 bg-slate-900/30 px-3 py-2">
                      <Users className="mt-0.5 h-4 w-4 shrink-0 text-slate-400" aria-hidden="true" />
                      <div className="min-w-0 flex-1">
                        <p className="text-sm text-slate-200">{manual.description}</p>
                        {manual.reason && <p className="mt-1 text-xs text-slate-400">Reason: {manual.reason}</p>}
                        {manual.component && <p className="text-xs text-slate-500">{manual.component}</p>}
                      </div>
                    </li>
                  ))}
                </ul>
              </Card>
            )}
          </div>

          {/* Risks */}
          {result.risks && result.risks.length > 0 && (
            <Card title="Testing Risks" description={`${result.risks.length} risk${result.risks.length === 1 ? "" : "s"}`}>
              <ul className="space-y-2" role="list">
                {result.risks.map((risk, i) => (
                  <li key={i} className="flex items-start gap-2 rounded-md border border-amber-500/20 bg-amber-500/5 px-3 py-2">
                    <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-400" aria-hidden="true" />
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <p className="text-sm text-slate-200">{risk.description}</p>
                        {risk.severity && (
                          <span className={`shrink-0 rounded px-1.5 py-0.5 text-xs font-medium ${
                            risk.severity === "critical" || risk.severity === "high" ? "bg-rose-500/10 text-rose-300" :
                            risk.severity === "medium" ? "bg-amber-500/10 text-amber-300" :
                            "bg-slate-800 text-slate-400"
                          }`}>
                            {risk.severity}
                          </span>
                        )}
                      </div>
                      {risk.mitigation && <p className="mt-1 text-xs text-slate-400">Mitigation: {risk.mitigation}</p>}
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
                    <span className="mt-1 h-1.5 w-1.5 shrink-0 rounded-full bg-emerald-400" aria-hidden="true" />
                    {rec}
                  </li>
                ))}
              </ul>
            </Card>
          )}

          {/* Graph context indicator */}
          <div className="text-xs text-slate-500">
            Graph context: {result.graph_context_used ? "Test plan grounded in architecture graph data" : "No graph data available — general QA practices used"}
          </div>
        </>
      )}

      {/* Evidence */}
      <EvidencePanel evidence={evidence} />
    </div>
  );
}
