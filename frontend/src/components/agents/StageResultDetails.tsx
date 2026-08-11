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
import { humanizeRelationship } from "../../lib/humanizeRelationship";
import type {
  ApiIntelligenceResult,
  DevelopmentPlanResult,
  DocumentationHealthResult,
  DocumentationPlanResult,
  PlanningResult,
  PRReviewResult,
  RepositoryUnderstandingResult,
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

/** The one place a Testing result renders "this specific item's component
 * claim wasn't found in this run's evidence" — reads only the backend's
 * own `verified` field (see RegressionTestResult etc.), never the
 * component name's text. Kept deliberately quiet (no red/alarming color)
 * since "unverified" describes evidence coverage, not a defect in the
 * test itself — the aggregate VerificationWarnings banner above already
 * carries the louder warning. */
const _SEVERITY_ORDER = ["critical", "high", "medium", "low"] as const;
const _SEVERITY_BAR_STYLES: Record<(typeof _SEVERITY_ORDER)[number], string> = {
  critical: "bg-danger-solid",
  high: "bg-danger-solid/70",
  medium: "bg-warning-solid",
  low: "bg-surface-raised",
};

/** A single glanceable bar in place of reading every risk card just to
 * learn "is this bad overall" (UX audit's visual-improvements section —
 * only added where the underlying data already carries a `severity`
 * field; Planning's own `risk_considerations` are plain strings with no
 * severity to visualize, so this is deliberately not used there). */
function RiskSeverityBar({ risks }: { risks: { severity: string }[] }) {
  const counts = _SEVERITY_ORDER.map((sev) => ({
    severity: sev,
    count: risks.filter((r) => r.severity === sev).length,
  })).filter((c) => c.count > 0);
  if (counts.length === 0) return null;
  const total = risks.length;
  return (
    <div className="mb-3 flex flex-col gap-1.5">
      <div className="flex h-2 w-full overflow-hidden rounded-full bg-surface-raised" role="img" aria-label={counts.map((c) => `${c.count} ${c.severity}`).join(", ")}>
        {counts.map((c) => (
          <div
            key={c.severity}
            className={_SEVERITY_BAR_STYLES[c.severity]}
            style={{ width: `${(c.count / total) * 100}%` }}
          />
        ))}
      </div>
      <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-fg-muted">
        {counts.map((c) => (
          <span key={c.severity} className="capitalize">
            {c.severity} <span className="font-medium text-fg-secondary">{c.count}</span>
          </span>
        ))}
      </div>
    </div>
  );
}

function UnverifiedBadge() {
  return (
    <span
      className="rounded bg-surface-raised px-1 text-fg-subtle"
      title="This component wasn't found in this run's indexed graph data — evidence coverage, not necessarily a problem with the test itself."
    >
      unverified
    </span>
  );
}

export function PlanningResultDetails({ result }: { result: PlanningResult }) {
  return (
    <>
      <GroundingBanner
        graphContextUsed={result.graph_context_used}
        groundingStatus={result.grounding_status}
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
        groundingStatus={result.grounding_status}
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
                        {/* file_path is only ever displayed once
                            file_path_verification says it's a real,
                            confirmed location — the field can otherwise
                            hold the model's own "not yet indexed"
                            placeholder, which used to render verbatim
                            here and read as a false claim about the
                            *component*, not just its unknown file path. */}
                        {comp.file_path && comp.file_path_verification === "verified"
                          ? ` • ${comp.file_path}`
                          : ""}
                        {comp.file_path_verification === "unverified" && (
                          <span className="ml-1 text-fg-subtle">· location unconfirmed</span>
                        )}
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
                  {humanizeRelationship(dep.relationship)}
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
          <RiskSeverityBar risks={result.risks} />
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
        groundingStatus={result.grounding_status}
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
                    {!test.verified && <UnverifiedBadge />}
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
                      {humanizeRelationship(test.relationship)}
                    </span>
                    <span>{test.target_component}</span>
                    {!test.verified && <UnverifiedBadge />}
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
                    {edge.component && !edge.verified && <UnverifiedBadge />}
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
          <RiskSeverityBar risks={result.risks} />
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
          <RiskSeverityBar risks={result.risks} />
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

// ---------------------------------------------------------------------------
// PR Review / Documentation Health / API Intelligence / Repository
// Understanding — extracted from ReviewPage/DocumentationHealthPage/
// ApiIntelligencePage/RepositoryUnderstandingPage's own inline result
// views (byte-identical rendering, just relocated) specifically so
// StageResultPanel can render the same summary for a run reached via Run
// History, not only the run just submitted in that browser session. See
// this file's own module docstring — same "one shared module" reasoning
// that already applied to Planning/Development/Testing.
// ---------------------------------------------------------------------------

const _FINDING_SEVERITY_STYLES: Record<string, string> = {
  critical: "border-danger-line/40 bg-danger-bg",
  high: "border-danger-line/20 bg-danger-bg/50",
  medium: "border-warning-line/30 bg-warning-bg",
};

function _mergeRecommendationStyles(recommendation: string): string {
  switch (recommendation) {
    case "approve":
      return "bg-success-bg text-success-fg ring-success-line/30";
    case "approve_with_comments":
      return "bg-warning-bg text-warning-fg ring-warning-line/30";
    case "request_changes":
      return "bg-danger-bg text-danger-fg ring-danger-line/30";
    case "block":
      return "bg-danger-bg text-danger-fg ring-danger-line/50";
    default:
      return "bg-surface text-fg-muted ring-line";
  }
}

function _mergeRecommendationLabel(recommendation: string): string {
  switch (recommendation) {
    case "approve":
      return "Approve";
    case "approve_with_comments":
      return "Approve with Comments";
    case "request_changes":
      return "Request Changes";
    case "block":
      return "Block";
    default:
      return recommendation;
  }
}

function _ObservationList({ label, items }: { label: string; items: string[] }) {
  if (items.length === 0) return null;
  return (
    <div>
      <h4 className="text-xs font-medium text-fg-muted">{label}</h4>
      <ul className="mt-1 space-y-1" role="list">
        {items.map((item, i) => (
          <li key={i} className="text-sm text-fg-secondary">
            • {item}
          </li>
        ))}
      </ul>
    </div>
  );
}

export function ReviewResultDetails({ result }: { result: PRReviewResult }) {
  const hasScorecard =
    result.quality_score != null || result.risk_score != null || Boolean(result.merge_recommendation);
  const severityOrder = ["critical", "high", "medium", "low"];
  const sortedFindings = [...(result.findings ?? [])].sort(
    (a, b) => severityOrder.indexOf(a.severity) - severityOrder.indexOf(b.severity),
  );

  return (
    <>
      {result.executive_summary && (
        <Card title="Review Summary">
          <p className="text-sm text-fg-secondary">{result.executive_summary}</p>
        </Card>
      )}

      {/* Scorecard first — the single most decision-relevant thing this
          agent produces (risk/quality/merge recommendation), so it's the
          first structured content after the one-sentence summary, not
          buried below findings. */}
      {hasScorecard && (
        <Card title="Scorecard">
          <div className="flex flex-wrap items-center gap-6">
            {result.quality_score != null && (
              <div>
                <dt className="text-xs text-fg-muted">Quality Score</dt>
                <dd className="text-2xl font-semibold text-fg">
                  {Math.round(result.quality_score)}
                  <span className="text-sm text-fg-muted">/100</span>
                </dd>
              </div>
            )}
            {result.risk_score != null && (
              <div>
                <dt className="text-xs text-fg-muted">Risk Score</dt>
                <dd className="text-2xl font-semibold text-fg">
                  {Math.round(result.risk_score)}
                  <span className="text-sm text-fg-muted">/100</span>
                </dd>
              </div>
            )}
            {result.merge_recommendation && (
              <div>
                <dt className="text-xs text-fg-muted">Merge Recommendation</dt>
                <dd className="mt-1">
                  <span
                    className={`rounded-full px-2.5 py-1 text-xs font-medium ring-1 ring-inset ${_mergeRecommendationStyles(result.merge_recommendation)}`}
                  >
                    {_mergeRecommendationLabel(result.merge_recommendation)}
                  </span>
                </dd>
              </div>
            )}
          </div>
        </Card>
      )}

      {sortedFindings.length > 0 && (
        <Card title="Findings" description={`${sortedFindings.length} found`}>
          <ul className="space-y-3" role="list">
            {sortedFindings.map((f, i) => (
              <li
                key={i}
                className={`rounded-lg border px-4 py-3 ${_FINDING_SEVERITY_STYLES[f.severity] ?? "border-line-muted bg-surface"}`}
              >
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium text-fg">{f.title}</span>
                  <span className="rounded-full bg-surface px-2 py-0.5 text-xs uppercase tracking-wide text-fg-muted ring-1 ring-inset ring-line">
                    {f.severity}
                  </span>
                  <span className="text-xs text-fg-muted">{f.category}</span>
                </div>
                <p className="mt-1 text-sm text-fg-secondary">{f.description}</p>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {(result.architecture_observations?.length > 0 ||
        result.maintainability_observations?.length > 0 ||
        result.reliability_observations?.length > 0) && (
        <Card title="Observations">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            <_ObservationList label="Architecture" items={result.architecture_observations} />
            <_ObservationList label="Maintainability" items={result.maintainability_observations} />
            <_ObservationList label="Reliability" items={result.reliability_observations} />
          </div>
        </Card>
      )}

      {(result.testing_review || result.documentation_review) && (
        <Card title="Testing & Documentation">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            {result.testing_review && (
              <div>
                <h4 className="text-xs font-medium text-fg-muted">Testing Review</h4>
                <p className="mt-1 text-sm text-fg-secondary">{result.testing_review}</p>
              </div>
            )}
            {result.documentation_review && (
              <div>
                <h4 className="text-xs font-medium text-fg-muted">Documentation Review</h4>
                <p className="mt-1 text-sm text-fg-secondary">{result.documentation_review}</p>
              </div>
            )}
          </div>
        </Card>
      )}

      {(result.positive_findings?.length > 0 || result.suggested_improvements?.length > 0) && (
        <Card title="What's Working & What to Improve">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <_ObservationList label="Positive Findings" items={result.positive_findings} />
            <_ObservationList label="Suggested Improvements" items={result.suggested_improvements} />
          </div>
        </Card>
      )}

      {result.breaking_changes?.length > 0 && (
        <Card title="Breaking Changes" description={`${result.breaking_changes.length} found`}>
          <ul className="space-y-3" role="list">
            {result.breaking_changes.map((bc, i) => (
              <li key={i} className="rounded-lg border border-danger-line/30 bg-danger-bg px-4 py-3">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium text-danger-fg">{bc.component}</span>
                  {bc.severity && (
                    <span className="rounded-full bg-danger-bg px-2 py-0.5 text-xs text-danger-fg ring-1 ring-inset ring-danger-line/30">
                      {bc.severity}
                    </span>
                  )}
                </div>
                <p className="mt-1 text-sm text-fg-secondary">{bc.description}</p>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {result.migration_advice?.length > 0 && (
        <Card
          title="Migration Advice"
          description={`${result.migration_advice.length} recommendation${result.migration_advice.length === 1 ? "" : "s"}`}
        >
          <ul className="space-y-2" role="list">
            {result.migration_advice.map((ma, i) => (
              <li key={i} className="rounded-lg border border-line-muted bg-surface px-4 py-3">
                <span className="text-xs font-medium text-fg-muted">{ma.component}</span>
                <p className="mt-0.5 text-sm text-fg-secondary">{ma.advice}</p>
                {ma.priority && (
                  <span className="mt-1 inline-block text-xs text-fg-muted">
                    Priority: {ma.priority}
                  </span>
                )}
              </li>
            ))}
          </ul>
        </Card>
      )}

      {result.suggested_reviewers?.length > 0 && (
        <Card title="Suggested Reviewers">
          <ul className="space-y-2" role="list">
            {result.suggested_reviewers.map((sr, i) => (
              <li key={i} className="flex items-center gap-3 text-sm">
                <span className="font-medium text-fg-secondary">{sr.reviewer}</span>
                <span className="text-fg-muted">—</span>
                <span className="text-fg-muted">{sr.reason}</span>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {result.regression_tests?.length > 0 && (
        <Card title="Regression Tests" description={`${result.regression_tests.length} suggested`}>
          <ul className="space-y-2" role="list">
            {result.regression_tests.map((rt, i) => (
              <li key={i} className="rounded-lg border border-line-muted bg-surface px-4 py-3">
                <span className="text-xs font-medium text-fg-muted">{rt.component}</span>
                <p className="mt-0.5 text-sm text-fg-secondary">{rt.test_description}</p>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </>
  );
}

const _DOC_HEALTH_GRADE_STYLES: Record<string, { ring: string; text: string; bg: string }> = {
  excellent: { ring: "ring-success-line/40", text: "text-success-fg", bg: "bg-success-bg" },
  good: { ring: "ring-success-line/30", text: "text-success-fg", bg: "bg-success-bg" },
  fair: { ring: "ring-warning-line/40", text: "text-warning-fg", bg: "bg-warning-bg" },
  poor: { ring: "ring-danger-line/30", text: "text-danger-fg", bg: "bg-danger-bg" },
  critical: { ring: "ring-danger-line/40", text: "text-danger-fg", bg: "bg-danger-bg" },
};

const _DOC_HEALTH_SEVERITY_STYLES: Record<string, string> = {
  high: "border-danger-line/30 bg-danger-bg text-danger-fg",
  medium: "border-warning-line/30 bg-warning-bg text-warning-fg",
  low: "border-line-muted bg-surface text-fg-muted",
};

const _DOC_HEALTH_CATEGORY_LABELS: Record<string, string> = {
  missing_readme: "Missing README",
  missing_architecture_doc: "Missing Architecture Doc",
  empty_document: "Empty Document",
  placeholder_document: "Placeholder Document",
  duplicate_document: "Duplicate Document",
  duplicate_section: "Duplicate Section",
  broken_link: "Broken Link",
  missing_toc: "Missing Table of Contents",
  undocumented_folder: "Undocumented Folder",
  missing_title: "Missing Title",
  missing_ownership: "Missing Ownership",
  missing_last_updated: "Missing Last Updated",
};

export function DocumentationHealthResultDetails({ result }: { result: DocumentationHealthResult }) {
  const score = result.health_score ?? 0;
  const grade = result.grade ?? "critical";
  const gradeStyle = _DOC_HEALTH_GRADE_STYLES[grade] ?? _DOC_HEALTH_GRADE_STYLES.critical;
  const stats = result.stats ?? {};

  return (
    <>
      <Card title="Overall Documentation Health">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center">
          <div
            className={`flex h-28 w-28 shrink-0 flex-col items-center justify-center rounded-full ${gradeStyle.bg} ring-4 ring-inset ${gradeStyle.ring}`}
            role="img"
            aria-label={`Health score ${score} out of 100, graded ${grade}`}
          >
            <span className={`font-display text-3xl font-semibold ${gradeStyle.text}`}>{score}</span>
            <span className="text-[10px] uppercase tracking-wide text-fg-muted">out of 100</span>
          </div>
          <div className="min-w-0 flex-1">
            <span
              className={`inline-flex items-center rounded-full ${gradeStyle.bg} px-2.5 py-1 text-[10px] font-medium uppercase tracking-wide ${gradeStyle.text}`}
            >
              {grade}
            </span>
            {result.summary && <p className="mt-2 text-sm text-fg-secondary">{result.summary}</p>}
          </div>
        </div>

        <dl className="mt-5 grid grid-cols-2 gap-x-6 gap-y-3 border-t border-line-muted pt-4 text-sm sm:grid-cols-4">
          <div>
            <dt className="text-xs text-fg-muted">Markdown files</dt>
            <dd className="text-fg-secondary">{stats.total_markdown_files ?? 0}</dd>
          </div>
          <div>
            <dt className="text-xs text-fg-muted">Directories</dt>
            <dd className="text-fg-secondary">{stats.distinct_doc_directories ?? 0}</dd>
          </div>
          <div>
            <dt className="text-xs text-fg-muted">Headings</dt>
            <dd className="text-fg-secondary">{stats.total_headings ?? 0}</dd>
          </div>
          <div>
            <dt className="text-xs text-fg-muted">ADRs</dt>
            <dd className="text-fg-secondary">{stats.adr_count ?? 0}</dd>
          </div>
        </dl>
      </Card>

      {result.score_breakdown?.length > 0 && (
        <Card title="Score Breakdown" description={`100 − penalties = ${score}`}>
          <ul className="space-y-1.5" role="list">
            {result.score_breakdown.map((c, i) => (
              <li key={i} className="flex items-center justify-between gap-3 text-sm">
                <span className="text-fg-secondary">
                  {_DOC_HEALTH_CATEGORY_LABELS[c.category] ?? c.category}
                  <span className="ml-2 text-xs text-fg-muted">
                    ×{c.finding_count}
                    {c.capped ? " (capped)" : ""}
                  </span>
                </span>
                <span className="font-mono text-xs text-danger-fg">−{c.penalty}</span>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {result.strengths?.length > 0 && (
        <Card title="Strengths">
          <ul className="list-inside list-disc space-y-1 text-sm text-fg-secondary" role="list">
            {result.strengths.map((s, i) => (
              <li key={i}>{s}</li>
            ))}
          </ul>
        </Card>
      )}

      {result.areas_for_improvement?.length > 0 && (
        <Card title="Areas for Improvement">
          <ul className="list-inside list-disc space-y-1 text-sm text-fg-secondary" role="list">
            {result.areas_for_improvement.map((s, i) => (
              <li key={i}>{s}</li>
            ))}
          </ul>
        </Card>
      )}

      {result.findings?.length > 0 && (
        <Card title="Findings" description={`${result.findings.length} found`}>
          <ul className="space-y-2" role="list">
            {result.findings.map((f, i) => (
              <li
                key={i}
                className={`rounded-lg border px-4 py-3 ${_DOC_HEALTH_SEVERITY_STYLES[f.severity] ?? _DOC_HEALTH_SEVERITY_STYLES.low}`}
              >
                <div className="flex items-center gap-2">
                  <span className="text-xs font-semibold uppercase tracking-wide">
                    {_DOC_HEALTH_CATEGORY_LABELS[f.category] ?? f.category}
                  </span>
                  <span className="font-mono text-xs opacity-80">{f.file_path}</span>
                </div>
                <p className="mt-1 text-sm">{f.message}</p>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {result.suggested_next_actions?.length > 0 && (
        <Card title="Suggested Next Actions">
          <ol className="list-inside list-decimal space-y-1 text-sm text-fg-secondary" role="list">
            {result.suggested_next_actions.map((s, i) => (
              <li key={i}>{s}</li>
            ))}
          </ol>
        </Card>
      )}

      <Card
        title="Files Reviewed"
        description={`${result.files_reviewed?.length ?? 0} Markdown file(s)`}
      >
        {!result.files_reviewed || result.files_reviewed.length === 0 ? (
          <p className="text-sm text-fg-muted">No Markdown files were found in this repository.</p>
        ) : (
          <ul className="space-y-1.5" role="list">
            {result.files_reviewed.map((f, i) => (
              <li key={i} className="flex items-center gap-2 text-sm">
                <span className="rounded bg-surface-raised px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-fg-muted">
                  {f.category}
                </span>
                <span className="font-mono text-xs text-fg-secondary">{f.path}</span>
                <span className="text-xs text-fg-muted">{f.heading_count} heading(s)</span>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </>
  );
}

const _API_METHOD_STYLES: Record<string, string> = {
  GET: "bg-success-bg text-success-fg",
  POST: "bg-cat-1-bg text-cat-1-fg",
  PUT: "bg-warning-bg text-warning-fg",
  PATCH: "bg-cat-2-bg text-cat-2-fg",
  DELETE: "bg-danger-bg text-danger-fg",
};

const _API_SEVERITY_STYLES: Record<string, string> = {
  critical: "border-danger-line/40 bg-danger-bg text-danger-fg",
  high: "border-danger-line/20 bg-danger-bg/60 text-danger-fg",
  medium: "border-warning-line/30 bg-warning-bg text-warning-fg",
  low: "border-line-muted bg-surface text-fg-muted",
};

export function ApiIntelligenceResultDetails({ result }: { result: ApiIntelligenceResult }) {
  const scores = result.scores ?? {};
  return (
    <>
      {result.executive_summary && (
        <Card title="Executive Summary">
          <p className="text-sm text-fg-secondary">{result.executive_summary}</p>
        </Card>
      )}

      {Object.keys(scores).length > 0 && (
        <Card title="Scores">
          <dl className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-6">
            {Object.entries(scores).map(([key, value]) => (
              <div key={key}>
                <dt className="text-xs capitalize text-fg-muted">{key.replaceAll("_", " ")}</dt>
                <dd className="text-xl font-semibold text-fg">
                  {value}
                  <span className="text-xs text-fg-muted">/100</span>
                </dd>
              </div>
            ))}
          </dl>
        </Card>
      )}

      {result.endpoints?.length > 0 && (
        <Card title="Endpoints" description={`${result.endpoints.length} documented`}>
          <ul className="space-y-2" role="list">
            {result.endpoints.map((e, i) => (
              <li key={i} className="rounded-lg border border-line-muted bg-surface px-4 py-3">
                <div className="flex items-center gap-2">
                  <span
                    className={`rounded px-2 py-0.5 text-xs font-semibold ${_API_METHOD_STYLES[e.method] ?? "bg-surface-raised text-fg-muted"}`}
                  >
                    {e.method}
                  </span>
                  <span className="font-mono text-sm text-fg-secondary">{e.path}</span>
                  {e.authentication_required && (
                    <span className="text-xs text-warning-fg">🔒 Auth</span>
                  )}
                </div>
                {e.description && <p className="mt-1 text-sm text-fg-muted">{e.description}</p>}
              </li>
            ))}
          </ul>
        </Card>
      )}

      {result.security_findings?.length > 0 && (
        <Card title="Security Findings" description={`${result.security_findings.length} found`}>
          <ul className="space-y-2" role="list">
            {result.security_findings.map((f, i) => (
              <li
                key={i}
                className={`rounded-lg border px-4 py-3 ${_API_SEVERITY_STYLES[f.severity] ?? _API_SEVERITY_STYLES.low}`}
              >
                <div className="flex items-center gap-2">
                  <span className="text-xs font-semibold uppercase tracking-wide">{f.severity}</span>
                  <span className="text-sm font-medium">{f.title}</span>
                </div>
                <p className="mt-1 text-sm">{f.description}</p>
                <p className="mt-1 text-xs opacity-80">Recommendation: {f.recommendation}</p>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {result.missing_information?.length > 0 && (
        <Card title="Missing Information" description="What should be added — never hallucinated.">
          <ul className="space-y-1.5" role="list">
            {result.missing_information.map((item, i) => (
              <li key={i} className="text-sm text-fg-muted">
                • {item}
              </li>
            ))}
          </ul>
        </Card>
      )}
    </>
  );
}

export function RepositoryUnderstandingResultDetails({
  result,
}: {
  result: RepositoryUnderstandingResult;
}) {
  const apis = result.apis ?? [];
  const databases = result.databases ?? [];
  const queues = result.queues ?? [];
  const integrations = result.integrations ?? [];
  const dependencies = result.dependencies ?? [];
  const interestingFindings = result.interesting_findings ?? [];

  return (
    <>
      <Card title="Executive Summary">
        <p className="text-sm text-fg-secondary">
          {result.executive_summary || "No summary available."}
        </p>
      </Card>

      {(result.repository_overview || result.architecture_overview) && (
        <Card title="Repository Overview">
          {result.repository_overview && (
            <p className="text-sm text-fg-secondary">{result.repository_overview}</p>
          )}
          {result.architecture_overview && (
            <p className="mt-2 text-sm text-fg-secondary">
              <span className="text-xs uppercase tracking-wide text-fg-muted">Architecture: </span>
              {result.architecture_overview}
            </p>
          )}
        </Card>
      )}

      <Card title="API Summary" description={result.api_summary || `${apis.length} exposed endpoint(s)`}>
        {apis.length === 0 ? (
          <p className="text-sm text-fg-muted">No exposed APIs found.</p>
        ) : (
          <ul className="space-y-1 text-sm text-fg-secondary" role="list">
            {apis.map((api, i) => (
              <li key={i} className="font-mono text-xs">
                {api}
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card
        title="Database Summary"
        description={result.database_summary || `${databases.length} owned table(s)`}
      >
        {databases.length === 0 ? (
          <p className="text-sm text-fg-muted">No owned databases found.</p>
        ) : (
          <ul className="space-y-1 text-sm text-fg-secondary" role="list">
            {databases.map((db, i) => (
              <li key={i} className="font-mono text-xs">
                {db}
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card
        title="Messaging Summary"
        description={result.messaging_summary || `${queues.length} queue/topic(s)`}
      >
        {queues.length === 0 ? (
          <p className="text-sm text-fg-muted">No queue or topic usage found.</p>
        ) : (
          <ul className="space-y-1 text-sm text-fg-secondary" role="list">
            {queues.map((q, i) => (
              <li key={i} className="font-mono text-xs">
                {q}
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card
        title="External Systems"
        description={result.external_systems_summary || `${integrations.length} integration(s)`}
      >
        {integrations.length === 0 ? (
          <p className="text-sm text-fg-muted">No outbound integrations found.</p>
        ) : (
          <ul className="space-y-1 text-sm text-fg-secondary" role="list">
            {integrations.map((i2, i) => (
              <li key={i} className="font-mono text-xs">
                {i2}
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card
        title="Dependency Summary"
        description={result.dependency_summary || `${dependencies.length} dependency(ies)`}
      >
        {dependencies.length === 0 ? (
          <p className="text-sm text-fg-muted">No dependencies found.</p>
        ) : (
          <ul className="space-y-1 text-sm text-fg-secondary" role="list">
            {dependencies.map((dep, i) => (
              <li key={i} className="font-mono text-xs">
                {dep}
              </li>
            ))}
          </ul>
        )}
      </Card>

      {interestingFindings.length > 0 && (
        <Card title="Interesting Findings" description={`${interestingFindings.length} found`}>
          <ul className="list-inside list-disc space-y-1 text-sm text-fg-secondary" role="list">
            {interestingFindings.map((item, i) => (
              <li key={i} className="font-mono text-xs">
                {item}
              </li>
            ))}
          </ul>
        </Card>
      )}
    </>
  );
}
