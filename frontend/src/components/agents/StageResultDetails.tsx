import { AlertTriangle, CheckCircle2, GitBranch, Layers, Recycle, Users, Zap } from "lucide-react";
import { Card } from "../Card";
import type { DevelopmentPlanResult, PlanningResult, TestPlanResult } from "../../types/agent";

/**
 * Full per-field rendering of each agent's structured result — extracted
 * verbatim from PlanningPage/DevelopmentPage/TestingPage (the standalone
 * "Products" tools already had this; the Workflow view only ever showed
 * count pills). One shared module, not three files, since all three are
 * the same kind of thing (a result-shape renderer) and none of them needs
 * to be swapped independently.
 *
 * Pure presentational: `{ result }` in, `<Card>`s out. No data fetching,
 * no run/evidence/header concerns — those stay owned by whichever page or
 * panel is already rendering that context.
 */

export function PlanningResultDetails({ result }: { result: PlanningResult }) {
  return (
    <>
      {result.executive_summary && (
        <Card title="Implementation Plan">
          <p className="text-sm text-slate-200">{result.executive_summary}</p>
        </Card>
      )}

      {result.implementation_steps && result.implementation_steps.length > 0 && (
        <Card
          title="Implementation Steps"
          description={`${result.implementation_steps.length} step${result.implementation_steps.length === 1 ? "" : "s"}`}
        >
          <ol className="space-y-3" role="list">
            {result.implementation_steps.map((s, i) => (
              <li
                key={i}
                className="flex gap-3 rounded-lg border border-slate-800 bg-slate-900/40 px-4 py-3"
              >
                <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-sky-500/10 text-xs font-semibold text-sky-300 ring-1 ring-inset ring-sky-500/30">
                  {s.order ?? i + 1}
                </span>
                <div className="min-w-0 flex-1">
                  <p className="text-sm text-slate-200">{s.description}</p>
                  {s.affected_component && (
                    <p className="mt-1 text-xs text-slate-500">
                      Component: <span className="text-slate-400">{s.affected_component}</span>
                    </p>
                  )}
                  {s.risk_note && (
                    <p className="mt-1 text-xs text-amber-400">Risk: {s.risk_note}</p>
                  )}
                </div>
              </li>
            ))}
          </ol>
        </Card>
      )}

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {result.affected_components && result.affected_components.length > 0 && (
          <Card title="Affected Components">
            <ul className="space-y-1">
              {result.affected_components.map((c) => (
                <li key={c} className="text-sm text-slate-300">
                  {c}
                </li>
              ))}
            </ul>
          </Card>
        )}

        {result.kafka_topics_involved && result.kafka_topics_involved.length > 0 && (
          <Card title="Kafka Topics">
            <ul className="space-y-1">
              {result.kafka_topics_involved.map((t) => (
                <li key={t} className="font-mono text-sm text-slate-300">
                  {t}
                </li>
              ))}
            </ul>
          </Card>
        )}

        {result.repositories_consulted && result.repositories_consulted.length > 0 && (
          <Card title="Repositories Consulted">
            <ul className="space-y-1">
              {result.repositories_consulted.map((r) => (
                <li key={r} className="text-sm text-slate-300">
                  {r}
                </li>
              ))}
            </ul>
          </Card>
        )}
      </div>

      {result.risk_considerations && result.risk_considerations.length > 0 && (
        <Card title="Risk Considerations">
          <ul className="space-y-2">
            {result.risk_considerations.map((risk, i) => (
              <li key={i} className="flex items-start gap-2 text-sm text-amber-200">
                <span
                  className="mt-1 h-1.5 w-1.5 shrink-0 rounded-full bg-amber-400"
                  aria-hidden="true"
                />
                {risk}
              </li>
            ))}
          </ul>
        </Card>
      )}

      <div className="text-xs text-slate-500">
        Graph context:{" "}
        {result.graph_context_used ? "Used architecture graph data" : "No graph data available"}
      </div>
    </>
  );
}

export function DevelopmentResultDetails({ result }: { result: DevelopmentPlanResult }) {
  return (
    <>
      {result.executive_summary && (
        <Card title="Implementation Blueprint">
          <p className="text-sm text-slate-200">{result.executive_summary}</p>
          {/* What did Development actually produce? A plan — never code. */}
          <p className="mt-2 text-xs text-slate-500">
            This stage produces a structured implementation plan only — no repositories, branches,
            or commits are created.
          </p>
        </Card>
      )}

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
                      <span
                        className={`rounded px-1.5 py-0.5 text-xs font-medium ${
                          phase.estimated_complexity === "high"
                            ? "bg-rose-500/10 text-rose-300"
                            : phase.estimated_complexity === "medium"
                              ? "bg-amber-500/10 text-amber-300"
                              : "bg-emerald-500/10 text-emerald-300"
                        }`}
                      >
                        {phase.estimated_complexity}
                      </span>
                    )}
                    {phase.affected_components.map((comp) => (
                      <span
                        key={comp}
                        className="rounded bg-slate-800 px-1.5 py-0.5 text-xs text-slate-400"
                      >
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

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {result.repositories && result.repositories.length > 0 && (
          <Card
            title="Affected Repositories"
            description={`${result.repositories.length} repo${result.repositories.length === 1 ? "" : "s"}`}
          >
            <ul className="space-y-2" role="list">
              {result.repositories.map((repo, i) => (
                <li
                  key={i}
                  className="flex items-start gap-2 rounded-md border border-slate-800 bg-slate-900/30 px-3 py-2"
                >
                  <GitBranch
                    className="mt-0.5 h-4 w-4 shrink-0 text-violet-400"
                    aria-hidden="true"
                  />
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium text-slate-200">{repo.name}</p>
                    {repo.reason && <p className="text-xs text-slate-400">{repo.reason}</p>}
                  </div>
                </li>
              ))}
            </ul>
          </Card>
        )}

        {result.components && result.components.length > 0 && (
          <Card
            title="Affected Components"
            description={`${result.components.length} component${result.components.length === 1 ? "" : "s"}`}
          >
            <ul className="space-y-2" role="list">
              {result.components.map((comp, i) => (
                <li
                  key={i}
                  className="flex items-start gap-2 rounded-md border border-slate-800 bg-slate-900/30 px-3 py-2"
                >
                  <Layers className="mt-0.5 h-4 w-4 shrink-0 text-sky-400" aria-hidden="true" />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <p className="text-sm font-medium text-slate-200">{comp.name}</p>
                      {comp.component_type && (
                        <span className="rounded bg-slate-800 px-1.5 py-0.5 text-xs text-slate-400">
                          {comp.component_type}
                        </span>
                      )}
                    </div>
                    {comp.change_description && (
                      <p className="mt-0.5 text-xs text-slate-400">{comp.change_description}</p>
                    )}
                    {comp.repository && (
                      <p className="text-xs text-slate-500">
                        {comp.repository}
                        {comp.file_path ? ` • ${comp.file_path}` : ""}
                      </p>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          </Card>
        )}
      </div>

      {result.dependencies && result.dependencies.length > 0 && (
        <Card
          title="Dependencies"
          description={`${result.dependencies.length} relationship${result.dependencies.length === 1 ? "" : "s"}`}
        >
          <ul className="space-y-2" role="list">
            {result.dependencies.map((dep, i) => (
              <li
                key={i}
                className="flex items-center gap-2 rounded-md border border-slate-800 bg-slate-900/30 px-3 py-2 text-sm"
              >
                <span className="text-slate-200">{dep.source}</span>
                <span className="rounded bg-sky-500/10 px-1.5 py-0.5 text-xs font-medium text-sky-300">
                  {dep.relationship}
                </span>
                <span className="text-slate-200">{dep.target}</span>
                {dep.risk_note && (
                  <span className="ml-auto text-xs text-amber-400">{dep.risk_note}</span>
                )}
              </li>
            ))}
          </ul>
        </Card>
      )}

      {result.reusable_implementations && result.reusable_implementations.length > 0 && (
        <Card title="Reuse Candidates" description="Existing implementations to leverage">
          <ul className="space-y-2" role="list">
            {result.reusable_implementations.map((impl, i) => (
              <li
                key={i}
                className="flex items-start gap-2 rounded-md border border-emerald-500/20 bg-emerald-500/5 px-3 py-2"
              >
                <Recycle className="mt-0.5 h-4 w-4 shrink-0 text-emerald-400" aria-hidden="true" />
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium text-emerald-200">{impl.name}</p>
                  {impl.reason && <p className="text-xs text-slate-400">{impl.reason}</p>}
                  {impl.repository && (
                    <p className="text-xs text-slate-500">in {impl.repository}</p>
                  )}
                </div>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {result.risks && result.risks.length > 0 && (
        <Card
          title="Risks"
          description={`${result.risks.length} identified risk${result.risks.length === 1 ? "" : "s"}`}
        >
          <ul className="space-y-2" role="list">
            {result.risks.map((risk, i) => (
              <li
                key={i}
                className="flex items-start gap-2 rounded-md border border-amber-500/20 bg-amber-500/5 px-3 py-2"
              >
                <AlertTriangle
                  className="mt-0.5 h-4 w-4 shrink-0 text-amber-400"
                  aria-hidden="true"
                />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <p className="text-sm text-slate-200">{risk.description}</p>
                    {risk.severity && (
                      <span
                        className={`shrink-0 rounded px-1.5 py-0.5 text-xs font-medium ${
                          risk.severity === "critical" || risk.severity === "high"
                            ? "bg-rose-500/10 text-rose-300"
                            : risk.severity === "medium"
                              ? "bg-amber-500/10 text-amber-300"
                              : "bg-slate-800 text-slate-400"
                        }`}
                      >
                        {risk.severity}
                      </span>
                    )}
                  </div>
                  {risk.mitigation && (
                    <p className="mt-1 text-xs text-slate-400">Mitigation: {risk.mitigation}</p>
                  )}
                  {risk.affected_component && (
                    <p className="text-xs text-slate-500">Affects: {risk.affected_component}</p>
                  )}
                </div>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {result.recommendations && result.recommendations.length > 0 && (
        <Card title="Recommendations">
          <ul className="space-y-2">
            {result.recommendations.map((rec, i) => (
              <li key={i} className="flex items-start gap-2 text-sm text-slate-300">
                <span
                  className="mt-1 h-1.5 w-1.5 shrink-0 rounded-full bg-violet-400"
                  aria-hidden="true"
                />
                {rec}
              </li>
            ))}
          </ul>
        </Card>
      )}

      <div className="text-xs text-slate-500">
        Graph context:{" "}
        {result.graph_context_used
          ? "Blueprint grounded in architecture graph data"
          : "No graph data available — general engineering practices used"}
      </div>
    </>
  );
}

export function TestingResultDetails({ result }: { result: TestPlanResult }) {
  return (
    <>
      {result.executive_summary && (
        <Card title="Test Strategy">
          <p className="text-sm text-slate-200">{result.executive_summary}</p>
        </Card>
      )}

      {result.test_scope &&
        (result.test_scope.in_scope.length > 0 || result.test_scope.out_of_scope.length > 0) && (
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            {result.test_scope.in_scope.length > 0 && (
              <Card title="In Scope">
                <ul className="space-y-1">
                  {result.test_scope.in_scope.map((item, i) => (
                    <li key={i} className="flex items-start gap-2 text-sm text-teal-200">
                      <CheckCircle2
                        className="mt-0.5 h-3.5 w-3.5 shrink-0 text-teal-400"
                        aria-hidden="true"
                      />
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
                    <li key={i} className="text-sm text-slate-400">
                      {item}
                    </li>
                  ))}
                </ul>
              </Card>
            )}
          </div>
        )}

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
                <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-teal-500/10 text-xs font-semibold text-teal-300 ring-1 ring-inset ring-teal-500/30">
                  {phase.order ?? i + 1}
                </span>
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium text-slate-200">{phase.title}</p>
                  <p className="mt-1 text-sm text-slate-400">{phase.description}</p>
                  <div className="mt-2 flex flex-wrap gap-2">
                    {phase.test_types.map((type) => (
                      <span
                        key={type}
                        className="rounded bg-teal-500/10 px-1.5 py-0.5 text-xs text-teal-300"
                      >
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

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {result.regression_tests && result.regression_tests.length > 0 && (
          <Card
            title="Regression Tests"
            description={`${result.regression_tests.length} test${result.regression_tests.length === 1 ? "" : "s"}`}
          >
            <ul className="space-y-2" role="list">
              {result.regression_tests.map((test, i) => (
                <li
                  key={i}
                  className="rounded-md border border-slate-800 bg-slate-900/30 px-3 py-2"
                >
                  <div className="flex items-center gap-2">
                    <p className="flex-1 text-sm text-slate-200">{test.description}</p>
                    {test.priority && (
                      <span
                        className={`shrink-0 rounded px-1.5 py-0.5 text-xs font-medium ${
                          test.priority === "critical"
                            ? "bg-rose-500/10 text-rose-300"
                            : test.priority === "high"
                              ? "bg-amber-500/10 text-amber-300"
                              : test.priority === "medium"
                                ? "bg-sky-500/10 text-sky-300"
                                : "bg-slate-800 text-slate-400"
                        }`}
                      >
                        {test.priority}
                      </span>
                    )}
                  </div>
                  <div className="mt-1 flex items-center gap-2 text-xs text-slate-500">
                    <span>{test.component}</span>
                    {test.automated && <span className="text-teal-400">automated</span>}
                  </div>
                </li>
              ))}
            </ul>
          </Card>
        )}

        {result.integration_tests && result.integration_tests.length > 0 && (
          <Card
            title="Integration Tests"
            description={`${result.integration_tests.length} test${result.integration_tests.length === 1 ? "" : "s"}`}
          >
            <ul className="space-y-2" role="list">
              {result.integration_tests.map((test, i) => (
                <li
                  key={i}
                  className="rounded-md border border-slate-800 bg-slate-900/30 px-3 py-2"
                >
                  <p className="text-sm text-slate-200">{test.description}</p>
                  <div className="mt-1 flex items-center gap-2 text-xs text-slate-500">
                    <span>{test.source_component}</span>
                    <span className="rounded bg-sky-500/10 px-1 text-sky-300">
                      {test.relationship}
                    </span>
                    <span>{test.target_component}</span>
                    {test.priority && (
                      <span
                        className={`ml-auto rounded px-1.5 py-0.5 text-xs font-medium ${
                          test.priority === "critical"
                            ? "bg-rose-500/10 text-rose-300"
                            : test.priority === "high"
                              ? "bg-amber-500/10 text-amber-300"
                              : "bg-slate-800 text-slate-400"
                        }`}
                      >
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

      {result.edge_cases && result.edge_cases.length > 0 && (
        <Card
          title="Edge Cases & Negative Scenarios"
          description={`${result.edge_cases.length} case${result.edge_cases.length === 1 ? "" : "s"}`}
        >
          <ul className="space-y-2" role="list">
            {result.edge_cases.map((edge, i) => (
              <li
                key={i}
                className="flex items-start gap-2 rounded-md border border-amber-500/20 bg-amber-500/5 px-3 py-2"
              >
                <AlertTriangle
                  className="mt-0.5 h-4 w-4 shrink-0 text-amber-400"
                  aria-hidden="true"
                />
                <div className="min-w-0 flex-1">
                  <p className="text-sm text-slate-200">{edge.description}</p>
                  <div className="mt-1 flex items-center gap-2 text-xs text-slate-500">
                    {edge.component && <span>{edge.component}</span>}
                    {edge.category && (
                      <span className="rounded bg-slate-800 px-1 text-slate-400">
                        {edge.category}
                      </span>
                    )}
                    {edge.severity && (
                      <span
                        className={`rounded px-1.5 py-0.5 font-medium ${
                          edge.severity === "critical" || edge.severity === "high"
                            ? "bg-rose-500/10 text-rose-300"
                            : "bg-amber-500/10 text-amber-300"
                        }`}
                      >
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

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {result.automation_candidates && result.automation_candidates.length > 0 && (
          <Card title="Automation Candidates" description="Tests to automate">
            <ul className="space-y-2" role="list">
              {result.automation_candidates.map((auto, i) => (
                <li
                  key={i}
                  className="flex items-start gap-2 rounded-md border border-teal-500/20 bg-teal-500/5 px-3 py-2"
                >
                  <Zap className="mt-0.5 h-4 w-4 shrink-0 text-teal-400" aria-hidden="true" />
                  <div className="min-w-0 flex-1">
                    <p className="text-sm text-teal-200">{auto.description}</p>
                    <div className="mt-1 flex items-center gap-2 text-xs text-slate-500">
                      {auto.component && <span>{auto.component}</span>}
                      {auto.test_type && (
                        <span className="rounded bg-teal-500/10 px-1 text-teal-300">
                          {auto.test_type}
                        </span>
                      )}
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
                <li
                  key={i}
                  className="flex items-start gap-2 rounded-md border border-slate-700 bg-slate-900/30 px-3 py-2"
                >
                  <Users className="mt-0.5 h-4 w-4 shrink-0 text-slate-400" aria-hidden="true" />
                  <div className="min-w-0 flex-1">
                    <p className="text-sm text-slate-200">{manual.description}</p>
                    {manual.reason && (
                      <p className="mt-1 text-xs text-slate-400">Reason: {manual.reason}</p>
                    )}
                    {manual.component && (
                      <p className="text-xs text-slate-500">{manual.component}</p>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          </Card>
        )}
      </div>

      {result.risks && result.risks.length > 0 && (
        <Card
          title="Testing Risks"
          description={`${result.risks.length} risk${result.risks.length === 1 ? "" : "s"}`}
        >
          <ul className="space-y-2" role="list">
            {result.risks.map((risk, i) => (
              <li
                key={i}
                className="flex items-start gap-2 rounded-md border border-amber-500/20 bg-amber-500/5 px-3 py-2"
              >
                <AlertTriangle
                  className="mt-0.5 h-4 w-4 shrink-0 text-amber-400"
                  aria-hidden="true"
                />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <p className="text-sm text-slate-200">{risk.description}</p>
                    {risk.severity && (
                      <span
                        className={`shrink-0 rounded px-1.5 py-0.5 text-xs font-medium ${
                          risk.severity === "critical" || risk.severity === "high"
                            ? "bg-rose-500/10 text-rose-300"
                            : risk.severity === "medium"
                              ? "bg-amber-500/10 text-amber-300"
                              : "bg-slate-800 text-slate-400"
                        }`}
                      >
                        {risk.severity}
                      </span>
                    )}
                  </div>
                  {risk.mitigation && (
                    <p className="mt-1 text-xs text-slate-400">Mitigation: {risk.mitigation}</p>
                  )}
                </div>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {result.recommendations && result.recommendations.length > 0 && (
        <Card title="Recommendations">
          <ul className="space-y-2">
            {result.recommendations.map((rec, i) => (
              <li key={i} className="flex items-start gap-2 text-sm text-slate-300">
                <span
                  className="mt-1 h-1.5 w-1.5 shrink-0 rounded-full bg-teal-400"
                  aria-hidden="true"
                />
                {rec}
              </li>
            ))}
          </ul>
        </Card>
      )}

      <div className="text-xs text-slate-500">
        Graph context:{" "}
        {result.graph_context_used
          ? "Test plan grounded in architecture graph data"
          : "No graph data available — general QA practices used"}
      </div>
    </>
  );
}
