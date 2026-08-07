import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { Search, Send, RotateCcw, History, ExternalLink } from "lucide-react";
import { Card } from "../components/Card";
import { EvidencePanel } from "../components/EvidencePanel";
import { ConfidenceBadge } from "../components/agents/ConfidenceBadge";
import { RunProgress } from "../components/agents/RunProgress";
import { RunStatusBadge } from "../components/agents/RunStatusBadge";
import { useAgentRun } from "../hooks/useAgentRun";
import { useAuth } from "../app/auth-context";
import { getReviewReportHtml } from "../lib/api/analysis";

const PR_URL_PATTERN = /^https:\/\/github\.com\/[\w.-]+\/[\w.-]+\/pull\/\d+\/?$/;

export function ReviewPage() {
  const [input, setInput] = useState("");
  const [validationError, setValidationError] = useState<string | null>(null);
  const { run, isSubmitting, error, submit, reset } = useAgentRun();

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    const trimmed = input.trim();

    if (!trimmed) {
      setValidationError("Please enter a GitHub PR URL.");
      return;
    }

    if (!PR_URL_PATTERN.test(trimmed)) {
      setValidationError("Please enter a valid GitHub PR URL (e.g. https://github.com/owner/repo/pull/123).");
      return;
    }

    setValidationError(null);
    submit({ subject_reference: trimmed, goal: "review_pr" });
  };

  const handleNewReview = () => {
    reset();
    setInput("");
    setValidationError(null);
  };

  const hasResult = run && (run.status === "completed" || run.status === "partial" || run.status === "failed");

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="rounded-lg bg-success-bg p-2 ring-1 ring-inset ring-success-line/30">
            <Search className="h-5 w-5 text-success-fg" aria-hidden="true" />
          </div>
          <div>
            <h1 className="text-xl font-semibold text-fg">Review Pull Request</h1>
            <p className="text-sm text-fg-muted">
              Submit a GitHub PR for AI-powered change impact analysis. Every finding is grounded in graph traversals — zero hallucination.
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
            <div>
              <label htmlFor="review-input" className="block text-sm font-medium text-fg-secondary">
                GitHub Pull Request URL
              </label>
              <input
                id="review-input"
                type="text"
                value={input}
                onChange={(e) => {
                  setInput(e.target.value);
                  if (validationError) setValidationError(null);
                }}
                disabled={isSubmitting}
                placeholder="https://github.com/owner/repo/pull/123"
                className="mt-2 w-full rounded-lg border border-line bg-surface-raised px-4 py-3 text-sm text-fg placeholder-fg-subtle focus:border-success-line disabled:opacity-50"
                aria-required="true"
                aria-invalid={!!validationError}
                aria-describedby={validationError ? "review-validation-error" : undefined}
              />
              {validationError && (
                <p id="review-validation-error" className="mt-1 text-xs text-danger-fg" role="alert">
                  {validationError}
                </p>
              )}
            </div>

            <div className="flex items-center gap-3">
              <button
                type="submit"
                disabled={isSubmitting || !input.trim()}
                className="inline-flex items-center gap-2 rounded-lg bg-success-solid px-4 py-2 text-sm font-medium text-success-on-solid transition-colors hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50"
                aria-label="Submit review request"
              >
                <Send className="h-4 w-4" aria-hidden="true" />
                {isSubmitting ? "Reviewing…" : "Start Review"}
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
            onClick={handleNewReview}
            className="ml-3 text-danger-fg underline hover:text-danger-fg"
          >
            Try again
          </button>
        </div>
      )}

      {/* Result */}
      {hasResult && <ReviewResultView run={run} onNewReview={handleNewReview} />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Result sub-component
// ---------------------------------------------------------------------------

function ReviewResultView({ run, onNewReview }: { run: NonNullable<ReturnType<typeof useAgentRun>["run"]>; onNewReview: () => void }) {
  const { token } = useAuth();
  const [isOpeningReport, setIsOpeningReport] = useState(false);
  const [reportError, setReportError] = useState<string | null>(null);
  const step = run.steps[0];
  const result = step?.result as Record<string, unknown> | undefined;
  const evidence = step?.evidence ?? [];

  // subject_id is "pr:<uuid>" for pull_request subjects (see
  // resolve_pr_subject in app/agents/review_adapter.py) - the visual
  // report endpoint keys off that same pull request id.
  const pullRequestId = run.subject.subject_id.startsWith("pr:")
    ? run.subject.subject_id.slice(3)
    : null;

  async function handleOpenReport() {
    if (!token || !pullRequestId) return;
    const reportWindow = window.open("", "_blank");
    setIsOpeningReport(true);
    setReportError(null);
    try {
      const html = await getReviewReportHtml(token, pullRequestId);
      const blobUrl = URL.createObjectURL(new Blob([html], { type: "text/html" }));
      if (reportWindow) {
        reportWindow.location.href = blobUrl;
      } else {
        setReportError("Pop-up blocked - allow pop-ups for this site to view the report.");
      }
    } catch (err) {
      reportWindow?.close();
      setReportError(err instanceof Error ? err.message : "Failed to load the visual report.");
    } finally {
      setIsOpeningReport(false);
    }
  }

  const summary = (result?.executive_summary as string) ?? "";
  const breakingChanges = (result?.breaking_changes as Array<Record<string, unknown>>) ?? [];
  const migrationAdvice = (result?.migration_advice as Array<Record<string, unknown>>) ?? [];
  const suggestedReviewers = (result?.suggested_reviewers as Array<Record<string, unknown>>) ?? [];
  const regressionTests = (result?.regression_tests as Array<Record<string, unknown>>) ?? [];

  const qualityScore = result?.quality_score as number | null | undefined;
  const riskScore = result?.risk_score as number | null | undefined;
  const mergeRecommendation = result?.merge_recommendation as string | null | undefined;
  const findings = (result?.findings as Array<Record<string, unknown>>) ?? [];
  const architectureObservations = (result?.architecture_observations as string[]) ?? [];
  const maintainabilityObservations = (result?.maintainability_observations as string[]) ?? [];
  const reliabilityObservations = (result?.reliability_observations as string[]) ?? [];
  const testingReview = (result?.testing_review as string) ?? "";
  const documentationReview = (result?.documentation_review as string) ?? "";
  const positiveFindings = (result?.positive_findings as string[]) ?? [];
  const suggestedImprovements = (result?.suggested_improvements as string[]) ?? [];

  const hasReviewScorecard =
    qualityScore != null || riskScore != null || Boolean(mergeRecommendation);
  const severityOrder = ["critical", "high", "medium", "low"];
  const sortedFindings = [...findings].sort(
    (a, b) =>
      severityOrder.indexOf(a.severity as string) - severityOrder.indexOf(b.severity as string),
  );

  return (
    <div className="flex flex-col gap-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <RunStatusBadge status={run.status} />
          {step?.confidence && (
            <ConfidenceBadge confidence={step.confidence} />
          )}
        </div>
        <div className="flex items-center gap-2">
          {result && pullRequestId && (
            <button
              type="button"
              onClick={() => void handleOpenReport()}
              disabled={isOpeningReport}
              title="Opens the full executive dashboard - score bars, filterable findings, per-file review cards"
              className="inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium text-fg-muted ring-1 ring-inset ring-line transition-colors hover:bg-surface-raised hover:text-fg-secondary disabled:cursor-not-allowed disabled:opacity-50"
            >
              <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
              {isOpeningReport ? "Opening…" : "View Visual Report"}
            </button>
          )}
          <button
            type="button"
            onClick={onNewReview}
            className="inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium text-fg-muted ring-1 ring-inset ring-line transition-colors hover:bg-surface-raised hover:text-fg-secondary"
          >
            <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />
            New Review
          </button>
        </div>
      </div>

      {reportError && (
        <div className="rounded-lg border border-danger-line/30 bg-danger-bg px-4 py-3 text-sm text-danger-fg">
          {reportError}
        </div>
      )}

      {/* Run metadata */}
      <Card title="Run Details">
        <dl className="grid grid-cols-2 gap-x-6 gap-y-3 text-sm sm:grid-cols-3 lg:grid-cols-4">
          <div>
            <dt className="text-xs text-fg-muted">Goal</dt>
            <dd className="text-fg-secondary">{run.goal}</dd>
          </div>
          <div>
            <dt className="text-xs text-fg-muted">Subject</dt>
            <dd className="truncate text-fg-secondary" title={run.subject.display_name}>
              {run.subject.display_name}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-fg-muted">Status</dt>
            <dd><RunStatusBadge status={run.status} /></dd>
          </div>
          <div>
            <dt className="text-xs text-fg-muted">Confidence</dt>
            <dd>
              {step?.confidence ? (
                <ConfidenceBadge confidence={step.confidence} showReasoning />
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

      {/* Review summary */}
      {summary && (
        <Card title="Review Summary">
          <p className="text-sm text-fg-secondary">{summary}</p>
        </Card>
      )}

      {/* Scorecard: quality/risk score + merge recommendation */}
      {hasReviewScorecard && (
        <Card title="Scorecard">
          <div className="flex flex-wrap items-center gap-6">
            {qualityScore != null && (
              <div>
                <dt className="text-xs text-fg-muted">Quality Score</dt>
                <dd className="text-2xl font-semibold text-fg">{Math.round(qualityScore)}<span className="text-sm text-fg-muted">/100</span></dd>
              </div>
            )}
            {riskScore != null && (
              <div>
                <dt className="text-xs text-fg-muted">Risk Score</dt>
                <dd className="text-2xl font-semibold text-fg">{Math.round(riskScore)}<span className="text-sm text-fg-muted">/100</span></dd>
              </div>
            )}
            {mergeRecommendation && (
              <div>
                <dt className="text-xs text-fg-muted">Merge Recommendation</dt>
                <dd className="mt-1">
                  <span className={`rounded-full px-2.5 py-1 text-xs font-medium ring-1 ring-inset ${mergeRecommendationStyles(mergeRecommendation)}`}>
                    {mergeRecommendationLabel(mergeRecommendation)}
                  </span>
                </dd>
              </div>
            )}
          </div>
        </Card>
      )}

      {/* Findings, grouped by severity */}
      {sortedFindings.length > 0 && (
        <Card title="Findings" description={`${sortedFindings.length} found`}>
          <ul className="space-y-3" role="list">
            {sortedFindings.map((f, i) => (
              <li key={i} className={`rounded-lg border px-4 py-3 ${findingSeverityStyles(f.severity as string)}`}>
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium text-fg">{f.title as string}</span>
                  <span className="rounded-full bg-surface px-2 py-0.5 text-xs uppercase tracking-wide text-fg-muted ring-1 ring-inset ring-line">
                    {f.severity as string}
                  </span>
                  <span className="text-xs text-fg-muted">{f.category as string}</span>
                </div>
                <p className="mt-1 text-sm text-fg-secondary">{f.description as string}</p>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {/* Observations */}
      {(architectureObservations.length > 0 ||
        maintainabilityObservations.length > 0 ||
        reliabilityObservations.length > 0) && (
        <Card title="Observations">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            <ObservationList label="Architecture" items={architectureObservations} />
            <ObservationList label="Maintainability" items={maintainabilityObservations} />
            <ObservationList label="Reliability" items={reliabilityObservations} />
          </div>
        </Card>
      )}

      {/* Testing & Documentation review */}
      {(testingReview || documentationReview) && (
        <Card title="Testing & Documentation">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            {testingReview && (
              <div>
                <h4 className="text-xs font-medium text-fg-muted">Testing Review</h4>
                <p className="mt-1 text-sm text-fg-secondary">{testingReview}</p>
              </div>
            )}
            {documentationReview && (
              <div>
                <h4 className="text-xs font-medium text-fg-muted">Documentation Review</h4>
                <p className="mt-1 text-sm text-fg-secondary">{documentationReview}</p>
              </div>
            )}
          </div>
        </Card>
      )}

      {/* Positive findings + suggested improvements */}
      {(positiveFindings.length > 0 || suggestedImprovements.length > 0) && (
        <Card title="What's Working & What to Improve">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <ObservationList label="Positive Findings" items={positiveFindings} />
            <ObservationList label="Suggested Improvements" items={suggestedImprovements} />
          </div>
        </Card>
      )}

      {/* Breaking changes */}
      {breakingChanges.length > 0 && (
        <Card title="Breaking Changes" description={`${breakingChanges.length} found`}>
          <ul className="space-y-3" role="list">
            {breakingChanges.map((bc, i) => (
              <li key={i} className="rounded-lg border border-danger-line/30 bg-danger-bg px-4 py-3">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium text-danger-fg">{bc.component as string}</span>
                  {Boolean(bc.severity) && (
                    <span className="rounded-full bg-danger-bg px-2 py-0.5 text-xs text-danger-fg ring-1 ring-inset ring-danger-line/30">
                      {bc.severity as string}
                    </span>
                  )}
                </div>
                <p className="mt-1 text-sm text-fg-secondary">{bc.description as string}</p>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {/* Migration advice */}
      {migrationAdvice.length > 0 && (
        <Card title="Migration Advice" description={`${migrationAdvice.length} recommendation${migrationAdvice.length === 1 ? "" : "s"}`}>
          <ul className="space-y-2" role="list">
            {migrationAdvice.map((ma, i) => (
              <li key={i} className="rounded-lg border border-line-muted bg-surface px-4 py-3">
                <span className="text-xs font-medium text-fg-muted">{ma.component as string}</span>
                <p className="mt-0.5 text-sm text-fg-secondary">{ma.advice as string}</p>
                {Boolean(ma.priority) && (
                  <span className="mt-1 inline-block text-xs text-fg-muted">Priority: {ma.priority as string}</span>
                )}
              </li>
            ))}
          </ul>
        </Card>
      )}

      {/* Suggested reviewers */}
      {suggestedReviewers.length > 0 && (
        <Card title="Suggested Reviewers">
          <ul className="space-y-2" role="list">
            {suggestedReviewers.map((sr, i) => (
              <li key={i} className="flex items-center gap-3 text-sm">
                <span className="font-medium text-fg-secondary">{sr.reviewer as string}</span>
                <span className="text-fg-muted">—</span>
                <span className="text-fg-muted">{sr.reason as string}</span>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {/* Regression tests */}
      {regressionTests.length > 0 && (
        <Card title="Regression Tests" description={`${regressionTests.length} suggested`}>
          <ul className="space-y-2" role="list">
            {regressionTests.map((rt, i) => (
              <li key={i} className="rounded-lg border border-line-muted bg-surface px-4 py-3">
                <span className="text-xs font-medium text-fg-muted">{rt.component as string}</span>
                <p className="mt-0.5 text-sm text-fg-secondary">{rt.test_description as string}</p>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {/* Evidence */}
      <EvidencePanel evidence={evidence} />
    </div>
  );
}

function ObservationList({ label, items }: { label: string; items: string[] }) {
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

function findingSeverityStyles(severity: string): string {
  switch (severity) {
    case "critical":
      return "border-danger-line/40 bg-danger-bg";
    case "high":
      return "border-danger-line/20 bg-danger-bg/50";
    case "medium":
      return "border-warning-line/30 bg-warning-bg";
    default:
      return "border-line-muted bg-surface";
  }
}

function mergeRecommendationStyles(recommendation: string): string {
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

function mergeRecommendationLabel(recommendation: string): string {
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
