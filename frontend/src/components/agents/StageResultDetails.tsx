import {
  AlertTriangle,
  CheckCircle2,
  Circle,
  FileText,
  GitBranch,
  Layers,
  Recycle,
  Users,
  Zap,
} from "lucide-react";
import { Card } from "../Card";
import { VerificationWarnings } from "./VerificationWarnings";
import { GroundingBanner } from "./GroundingBanner";
import type {
  DevelopmentPlanResult,
  DocumentationPlanResult,
  PlanningResult,
  TestPlanResult,
} from "../../types/agent";

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
      <GroundingBanner
        graphContextUsed={result.graph_context_used}
        repositoriesConsulted={result.repositories_consulted}
        subject="plan"
      />

      <VerificationWarnings warnings={result.verification_warnings} subject="plan" />

      {result.executive_summary && (
        <Card title="Implementation Plan">
          <p className="text-sm text-fg-secondary">{result.executive_summary}</p>
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
                className="flex gap-3 rounded-lg border border-line-muted bg-surface px-4 py-3"
              >
                <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-info-bg text-xs font-semibold text-info-fg ring-1 ring-inset ring-info-line/30">
                  {s.order ?? i + 1}
                </span>
                <div className="min-w-0 flex-1">
                  <p className="text-sm text-fg-secondary">{s.description}</p>
                  {s.affected_component && (
                    <p className="mt-1 text-xs text-fg-muted">
                      Component: <span className="text-fg-muted">{s.affected_component}</span>
                    </p>
                  )}
                  {s.risk_note && (
                    <p className="mt-1 text-xs text-warning-fg">Risk: {s.risk_note}</p>
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
                <li key={c} className="text-sm text-fg-secondary">
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
                <li key={t} className="font-mono text-sm text-fg-secondary">
                  {t}
                </li>
              ))}
            </ul>
          </Card>
        )}

        {/* "Repositories Consulted" used to be a third card here. The
            GroundingBanner at the top of this result now names the same
            repositories as part of the grounding claim, where they carry
            more meaning — listing them twice on one screen was the
            duplicate-information problem, not thoroughness. */}
      </div>

      {result.risk_considerations && result.risk_considerations.length > 0 && (
        <Card title="Risk Considerations">
          <ul className="space-y-2">
            {result.risk_considerations.map((risk, i) => (
              <li key={i} className="flex items-start gap-2 text-sm text-warning-fg">
                <span
                  className="mt-1 h-1.5 w-1.5 shrink-0 rounded-full bg-warning-solid"
                  aria-hidden="true"
                />
                {risk}
              </li>
            ))}
          </ul>
        </Card>
      )}

    </>
  );
}

export function DevelopmentResultDetails({ result }: { result: DevelopmentPlanResult }) {
  return (
    <>
      <GroundingBanner
        graphContextUsed={result.graph_context_used}
        repositoriesConsulted={result.repositories_consulted}
        subject="blueprint"
      />

      <VerificationWarnings
        warnings={result.verification_warnings}
        subject="implementation plan"
      />

      {result.executive_summary && (
        <Card title="Implementation Blueprint">
          <p className="text-sm text-fg-secondary">{result.executive_summary}</p>
          {/* What did Development actually produce? A plan — never code. */}
          <p className="mt-2 text-xs text-fg-muted">
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
                className="flex gap-3 rounded-lg border border-line-muted bg-surface px-4 py-3"
              >
                <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-cat-7-bg text-xs font-semibold text-cat-7-fg ring-1 ring-inset ring-cat-7-line/30">
                  {phase.order ?? i + 1}
                </span>
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium text-fg-secondary">{phase.title}</p>
                  <p className="mt-1 text-sm text-fg-muted">{phase.description}</p>
                  <div className="mt-2 flex flex-wrap items-center gap-2">
                    {phase.estimated_complexity && (
                      <span
                        className={`rounded px-1.5 py-0.5 text-xs font-medium ${
                          phase.estimated_complexity === "high"
                            ? "bg-danger-bg text-danger-fg"
                            : phase.estimated_complexity === "medium"
                              ? "bg-warning-bg text-warning-fg"
                              : "bg-success-bg text-success-fg"
                        }`}
                      >
                        {phase.estimated_complexity}
                      </span>
                    )}
                    {phase.affected_components.map((comp) => (
                      <span
                        key={comp}
                        className="rounded bg-surface-raised px-1.5 py-0.5 text-xs text-fg-muted"
                      >
                        {comp}
                      </span>
                    ))}
                    {phase.depends_on_phases.length > 0 && (
                      <span className="text-xs text-fg-muted">
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
                  className="flex items-start gap-2 rounded-md border border-line-muted bg-surface px-3 py-2"
                >
                  <GitBranch
                    className="mt-0.5 h-4 w-4 shrink-0 text-cat-7-fg"
                    aria-hidden="true"
                  />
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium text-fg-secondary">{repo.name}</p>
                    {repo.reason && <p className="text-xs text-fg-muted">{repo.reason}</p>}
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
                  className="flex items-start gap-2 rounded-md border border-line-muted bg-surface px-3 py-2"
                >
                  <Layers className="mt-0.5 h-4 w-4 shrink-0 text-info-fg" aria-hidden="true" />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <p className="text-sm font-medium text-fg-secondary">{comp.name}</p>
                      {comp.component_type && (
                        <span className="rounded bg-surface-raised px-1.5 py-0.5 text-xs text-fg-muted">
                          {comp.component_type}
                        </span>
                      )}
                    </div>
                    {comp.change_description && (
                      <p className="mt-0.5 text-xs text-fg-muted">{comp.change_description}</p>
                    )}
                    {comp.repository && (
                      <p className="text-xs text-fg-muted">
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
                className="flex items-center gap-2 rounded-md border border-line-muted bg-surface px-3 py-2 text-sm"
              >
                <span className="text-fg-secondary">{dep.source}</span>
                <span className="rounded bg-info-bg px-1.5 py-0.5 text-xs font-medium text-info-fg">
                  {dep.relationship}
                </span>
                <span className="text-fg-secondary">{dep.target}</span>
                {dep.risk_note && (
                  <span className="ml-auto text-xs text-warning-fg">{dep.risk_note}</span>
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
                className="flex items-start gap-2 rounded-md border border-success-line/20 bg-success-bg px-3 py-2"
              >
                <Recycle className="mt-0.5 h-4 w-4 shrink-0 text-success-fg" aria-hidden="true" />
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium text-success-fg">{impl.name}</p>
                  {impl.reason && <p className="text-xs text-fg-muted">{impl.reason}</p>}
                  {impl.repository && (
                    <p className="text-xs text-fg-muted">in {impl.repository}</p>
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
                className="flex items-start gap-2 rounded-md border border-warning-line/20 bg-warning-bg px-3 py-2"
              >
                <AlertTriangle
                  className="mt-0.5 h-4 w-4 shrink-0 text-warning-fg"
                  aria-hidden="true"
                />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <p className="text-sm text-fg-secondary">{risk.description}</p>
                    {risk.severity && (
                      <span
                        className={`shrink-0 rounded px-1.5 py-0.5 text-xs font-medium ${
                          risk.severity === "critical" || risk.severity === "high"
                            ? "bg-danger-bg text-danger-fg"
                            : risk.severity === "medium"
                              ? "bg-warning-bg text-warning-fg"
                              : "bg-surface-raised text-fg-muted"
                        }`}
                      >
                        {risk.severity}
                      </span>
                    )}
                  </div>
                  {risk.mitigation && (
                    <p className="mt-1 text-xs text-fg-muted">Mitigation: {risk.mitigation}</p>
                  )}
                  {risk.affected_component && (
                    <p className="text-xs text-fg-muted">Affects: {risk.affected_component}</p>
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
              <li key={i} className="flex items-start gap-2 text-sm text-fg-secondary">
                <span
                  className="mt-1 h-1.5 w-1.5 shrink-0 rounded-full bg-cat-7-line"
                  aria-hidden="true"
                />
                {rec}
              </li>
            ))}
          </ul>
        </Card>
      )}

    </>
  );
}

export function TestingResultDetails({ result }: { result: TestPlanResult }) {
  return (
    <>
      <GroundingBanner
        graphContextUsed={result.graph_context_used}
        repositoriesConsulted={result.repositories_consulted}
        subject="test plan"
      />

      <VerificationWarnings warnings={result.verification_warnings} subject="test plan" />

      {result.executive_summary && (
        <Card title="Test Strategy">
          <p className="text-sm text-fg-secondary">{result.executive_summary}</p>
        </Card>
      )}

      {result.test_scope &&
        (result.test_scope.in_scope.length > 0 || result.test_scope.out_of_scope.length > 0) && (
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            {result.test_scope.in_scope.length > 0 && (
              <Card title="In Scope">
                <ul className="space-y-1">
                  {result.test_scope.in_scope.map((item, i) => (
                    <li key={i} className="flex items-start gap-2 text-sm text-cat-5-fg">
                      <CheckCircle2
                        className="mt-0.5 h-3.5 w-3.5 shrink-0 text-cat-5-fg"
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
                    <li key={i} className="text-sm text-fg-muted">
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
                className="flex gap-3 rounded-lg border border-line-muted bg-surface px-4 py-3"
              >
                <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-cat-5-bg text-xs font-semibold text-cat-5-fg ring-1 ring-inset ring-cat-5-line/30">
                  {phase.order ?? i + 1}
                </span>
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium text-fg-secondary">{phase.title}</p>
                  <p className="mt-1 text-sm text-fg-muted">{phase.description}</p>
                  <div className="mt-2 flex flex-wrap gap-2">
                    {phase.test_types.map((type) => (
                      <span
                        key={type}
                        className="rounded bg-cat-5-bg px-1.5 py-0.5 text-xs text-cat-5-fg"
                      >
                        {type}
                      </span>
                    ))}
                    {phase.depends_on_phases.length > 0 && (
                      <span className="text-xs text-fg-muted">
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
                  className="rounded-md border border-line-muted bg-surface px-3 py-2"
                >
                  <div className="flex items-center gap-2">
                    <p className="flex-1 text-sm text-fg-secondary">{test.description}</p>
                    {test.priority && (
                      <span
                        className={`shrink-0 rounded px-1.5 py-0.5 text-xs font-medium ${
                          test.priority === "critical"
                            ? "bg-danger-bg text-danger-fg"
                            : test.priority === "high"
                              ? "bg-warning-bg text-warning-fg"
                              : test.priority === "medium"
                                ? "bg-info-bg text-info-fg"
                                : "bg-surface-raised text-fg-muted"
                        }`}
                      >
                        {test.priority}
                      </span>
                    )}
                  </div>
                  <div className="mt-1 flex items-center gap-2 text-xs text-fg-muted">
                    <span>{test.component}</span>
                    {test.automated && <span className="text-cat-5-fg">automated</span>}
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
                  className="rounded-md border border-line-muted bg-surface px-3 py-2"
                >
                  <p className="text-sm text-fg-secondary">{test.description}</p>
                  <div className="mt-1 flex items-center gap-2 text-xs text-fg-muted">
                    <span>{test.source_component}</span>
                    <span className="rounded bg-info-bg px-1 text-info-fg">
                      {test.relationship}
                    </span>
                    <span>{test.target_component}</span>
                    {test.priority && (
                      <span
                        className={`ml-auto rounded px-1.5 py-0.5 text-xs font-medium ${
                          test.priority === "critical"
                            ? "bg-danger-bg text-danger-fg"
                            : test.priority === "high"
                              ? "bg-warning-bg text-warning-fg"
                              : "bg-surface-raised text-fg-muted"
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
                className="flex items-start gap-2 rounded-md border border-warning-line/20 bg-warning-bg px-3 py-2"
              >
                <AlertTriangle
                  className="mt-0.5 h-4 w-4 shrink-0 text-warning-fg"
                  aria-hidden="true"
                />
                <div className="min-w-0 flex-1">
                  <p className="text-sm text-fg-secondary">{edge.description}</p>
                  <div className="mt-1 flex items-center gap-2 text-xs text-fg-muted">
                    {edge.component && <span>{edge.component}</span>}
                    {edge.category && (
                      <span className="rounded bg-surface-raised px-1 text-fg-muted">
                        {edge.category}
                      </span>
                    )}
                    {edge.severity && (
                      <span
                        className={`rounded px-1.5 py-0.5 font-medium ${
                          edge.severity === "critical" || edge.severity === "high"
                            ? "bg-danger-bg text-danger-fg"
                            : "bg-warning-bg text-warning-fg"
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
                  className="flex items-start gap-2 rounded-md border border-cat-5-line/20 bg-cat-5-bg px-3 py-2"
                >
                  <Zap className="mt-0.5 h-4 w-4 shrink-0 text-cat-5-fg" aria-hidden="true" />
                  <div className="min-w-0 flex-1">
                    <p className="text-sm text-cat-5-fg">{auto.description}</p>
                    <div className="mt-1 flex items-center gap-2 text-xs text-fg-muted">
                      {auto.component && <span>{auto.component}</span>}
                      {auto.test_type && (
                        <span className="rounded bg-cat-5-bg px-1 text-cat-5-fg">
                          {auto.test_type}
                        </span>
                      )}
                    </div>
                    {auto.reason && <p className="mt-1 text-xs text-fg-muted">{auto.reason}</p>}
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
                  className="flex items-start gap-2 rounded-md border border-line bg-surface px-3 py-2"
                >
                  <Users className="mt-0.5 h-4 w-4 shrink-0 text-fg-muted" aria-hidden="true" />
                  <div className="min-w-0 flex-1">
                    <p className="text-sm text-fg-secondary">{manual.description}</p>
                    {manual.reason && (
                      <p className="mt-1 text-xs text-fg-muted">Reason: {manual.reason}</p>
                    )}
                    {manual.component && (
                      <p className="text-xs text-fg-muted">{manual.component}</p>
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
                className="flex items-start gap-2 rounded-md border border-warning-line/20 bg-warning-bg px-3 py-2"
              >
                <AlertTriangle
                  className="mt-0.5 h-4 w-4 shrink-0 text-warning-fg"
                  aria-hidden="true"
                />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <p className="text-sm text-fg-secondary">{risk.description}</p>
                    {risk.severity && (
                      <span
                        className={`shrink-0 rounded px-1.5 py-0.5 text-xs font-medium ${
                          risk.severity === "critical" || risk.severity === "high"
                            ? "bg-danger-bg text-danger-fg"
                            : risk.severity === "medium"
                              ? "bg-warning-bg text-warning-fg"
                              : "bg-surface-raised text-fg-muted"
                        }`}
                      >
                        {risk.severity}
                      </span>
                    )}
                  </div>
                  {risk.mitigation && (
                    <p className="mt-1 text-xs text-fg-muted">Mitigation: {risk.mitigation}</p>
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
              <li key={i} className="flex items-start gap-2 text-sm text-fg-secondary">
                <span
                  className="mt-1 h-1.5 w-1.5 shrink-0 rounded-full bg-cat-5-line"
                  aria-hidden="true"
                />
                {rec}
              </li>
            ))}
          </ul>
        </Card>
      )}

    </>
  );
}

const DOCUMENTATION_IMPACT_STYLES: Record<string, string> = {
  high: "bg-danger-bg text-danger-fg",
  medium: "bg-warning-bg text-warning-fg",
  low: "bg-info-bg text-info-fg",
  none: "bg-surface-raised text-fg-muted",
};

const PRIORITY_STYLES: Record<string, string> = {
  high: "bg-danger-bg text-danger-fg",
  medium: "bg-warning-bg text-warning-fg",
  low: "bg-surface-raised text-fg-muted",
};

export function DocumentationPlanningResultDetails({
  result,
}: {
  result: DocumentationPlanResult;
}) {
  return (
    <>
      <VerificationWarnings
        warnings={result.prior_verification_warnings}
        subject="documentation plan"
      />

      {result.executive_summary && (
        <Card title="Documentation Plan">
          <p className="text-sm text-fg-secondary">{result.executive_summary}</p>
          {result.documentation_impact && (
            <div className="mt-3 flex items-center gap-2">
              <span
                className={`rounded px-1.5 py-0.5 text-xs font-medium uppercase tracking-wide ${
                  DOCUMENTATION_IMPACT_STYLES[result.documentation_impact] ??
                  "bg-surface-raised text-fg-muted"
                }`}
              >
                {result.documentation_impact} impact
              </span>
              {result.impact_explanation && (
                <span className="text-xs text-fg-muted">{result.impact_explanation}</span>
              )}
            </div>
          )}
        </Card>
      )}

      {result.required_updates && result.required_updates.length > 0 && (
        <Card
          title="Required Documentation Updates"
          description={`${result.required_updates.length} document${result.required_updates.length === 1 ? "" : "s"}`}
        >
          <ul className="space-y-2" role="list">
            {result.required_updates.map((update, i) => (
              <li
                key={i}
                className="rounded-md border border-line-muted bg-surface px-3 py-2"
              >
                <div className="flex items-center gap-2">
                  <FileText className="h-3.5 w-3.5 shrink-0 text-fg-muted" aria-hidden="true" />
                  <p className="flex-1 truncate text-sm font-medium text-fg-secondary">
                    {update.document}
                  </p>
                  {update.action && (
                    <span className="shrink-0 rounded bg-surface-raised px-1.5 py-0.5 text-xs text-fg-muted">
                      {update.action}
                    </span>
                  )}
                  {update.priority && (
                    <span
                      className={`shrink-0 rounded px-1.5 py-0.5 text-xs font-medium ${
                        PRIORITY_STYLES[update.priority] ?? "bg-surface-raised text-fg-muted"
                      }`}
                    >
                      {update.priority}
                    </span>
                  )}
                </div>
                {update.reason && <p className="mt-1 text-xs text-fg-muted">{update.reason}</p>}
                <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-fg-muted">
                  {update.owner && <span>Owner: {update.owner}</span>}
                  {update.estimated_effort && <span>Effort: {update.estimated_effort}</span>}
                  {update.dependencies.length > 0 && (
                    <span>Depends on: {update.dependencies.join(", ")}</span>
                  )}
                </div>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {result.new_documentation && result.new_documentation.length > 0 && (
        <Card
          title="New Documentation"
          description={`${result.new_documentation.length} document${result.new_documentation.length === 1 ? "" : "s"}`}
        >
          <ul className="space-y-2" role="list">
            {result.new_documentation.map((doc, i) => (
              <li
                key={i}
                className="flex items-start gap-2 rounded-md border border-cat-5-line/20 bg-cat-5-bg px-3 py-2"
              >
                <Zap className="mt-0.5 h-4 w-4 shrink-0 text-cat-5-fg" aria-hidden="true" />
                <div className="min-w-0 flex-1">
                  <p className="text-sm text-cat-5-fg">{doc.name}</p>
                  {doc.purpose && <p className="mt-1 text-xs text-fg-muted">{doc.purpose}</p>}
                  <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-fg-muted">
                    {doc.suggested_location && <span>{doc.suggested_location}</span>}
                    {doc.owner && <span>Owner: {doc.owner}</span>}
                    {doc.estimated_effort && <span>Effort: {doc.estimated_effort}</span>}
                  </div>
                </div>
                {doc.priority && (
                  <span
                    className={`shrink-0 rounded px-1.5 py-0.5 text-xs font-medium ${
                      PRIORITY_STYLES[doc.priority] ?? "bg-surface-raised text-fg-muted"
                    }`}
                  >
                    {doc.priority}
                  </span>
                )}
              </li>
            ))}
          </ul>
        </Card>
      )}

      {result.existing_updates && result.existing_updates.length > 0 && (
        <Card title="Section-Level Detail">
          <ul className="space-y-2" role="list">
            {result.existing_updates.map((update, i) => (
              <li
                key={i}
                className="rounded-md border border-line-muted bg-surface px-3 py-2"
              >
                <p className="text-sm font-medium text-fg-secondary">{update.file_path}</p>
                {update.sections_affected.length > 0 && (
                  <div className="mt-1 flex flex-wrap gap-1">
                    {update.sections_affected.map((section) => (
                      <span
                        key={section}
                        className="rounded bg-surface-raised px-1.5 py-0.5 text-xs text-fg-muted"
                      >
                        {section}
                      </span>
                    ))}
                  </div>
                )}
                {update.summary_of_changes && (
                  <p className="mt-1 text-xs text-fg-muted">{update.summary_of_changes}</p>
                )}
              </li>
            ))}
          </ul>
        </Card>
      )}

      {result.risks && result.risks.length > 0 && (
        <Card
          title="Documentation Risks"
          description={`${result.risks.length} risk${result.risks.length === 1 ? "" : "s"}`}
        >
          <ul className="space-y-2" role="list">
            {result.risks.map((risk, i) => (
              <li
                key={i}
                className="flex items-start gap-2 rounded-md border border-warning-line/20 bg-warning-bg px-3 py-2"
              >
                <AlertTriangle
                  className="mt-0.5 h-4 w-4 shrink-0 text-warning-fg"
                  aria-hidden="true"
                />
                <div className="min-w-0 flex-1">
                  <p className="text-sm text-fg-secondary">{risk.description}</p>
                </div>
                {risk.severity && (
                  <span
                    className={`shrink-0 rounded px-1.5 py-0.5 text-xs font-medium ${
                      risk.severity === "critical" || risk.severity === "high"
                        ? "bg-danger-bg text-danger-fg"
                        : risk.severity === "medium"
                          ? "bg-warning-bg text-warning-fg"
                          : "bg-surface-raised text-fg-muted"
                    }`}
                  >
                    {risk.severity}
                  </span>
                )}
              </li>
            ))}
          </ul>
        </Card>
      )}

      {result.recommendations && result.recommendations.length > 0 && (
        <Card title="Recommendations">
          <ul className="space-y-2">
            {result.recommendations.map((rec, i) => (
              <li key={i} className="flex items-start gap-2 text-sm text-fg-secondary">
                <span
                  className="mt-1 h-1.5 w-1.5 shrink-0 rounded-full bg-cat-5-line"
                  aria-hidden="true"
                />
                {rec}
              </li>
            ))}
          </ul>
        </Card>
      )}

      {result.release_notes_draft && result.release_notes_draft.length > 0 && (
        <Card title="Release Notes Draft">
          <ul className="space-y-2">
            {result.release_notes_draft.map((note, i) => (
              <li key={i} className="flex items-start gap-2 text-sm text-fg-secondary">
                <span
                  className="mt-1 h-1.5 w-1.5 shrink-0 rounded-full bg-info-solid"
                  aria-hidden="true"
                />
                {note}
              </li>
            ))}
          </ul>
        </Card>
      )}

      {result.checklist && result.checklist.length > 0 && (
        <Card title="Documentation Checklist">
          <ul className="space-y-1.5" role="list">
            {result.checklist.map((item, i) => (
              <li
                key={i}
                className={`flex items-center gap-2 text-sm ${
                  item.applicable ? "text-fg-secondary" : "text-fg-subtle line-through"
                }`}
              >
                <Circle className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                {item.label}
                {!item.applicable && <span className="text-xs">(not applicable)</span>}
              </li>
            ))}
          </ul>
        </Card>
      )}
    </>
  );
}
