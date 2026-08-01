import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { AiModelSelector } from "../components/AiModelSelector";
import { Card } from "../components/Card";
import { ReasoningLogPanel } from "../components/ReasoningLogPanel";
import { RiskBadge } from "../components/RiskBadge";
import { StatusBadge } from "../components/StatusBadge";
import { useAiModel } from "../app/ai-model-context";
import { useAuth } from "../app/auth-context";
import { ApiError } from "../lib/api/client";
import {
  getAiAnalysis,
  getDeterministicAnalysis,
  getReviewReportHtml,
  investigatePullRequest,
  publishReview,
  runAiAnalysis,
  runDeterministicAnalysis,
} from "../lib/api/analysis";
import { usePullRequestsData } from "../hooks/usePullRequestsData";
import { findAiModel, type AiModelId } from "../types/aiModel";
import type {
  AIAnalysis,
  AIAnalysisResult,
  ImpactedNode,
  PullRequestAnalysis,
  ReasoningStep,
  RiskLevel,
} from "../types/analysis";

async function orNull<T>(promise: Promise<T>): Promise<T | null> {
  try {
    return await promise;
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) {
      return null;
    }
    throw err;
  }
}

const RISK_TONE: Record<RiskLevel, "danger" | "warning" | "success"> = {
  HIGH: "danger",
  MEDIUM: "warning",
  LOW: "success",
};

function ImpactedNodeList({ nodes, emptyLabel }: { nodes: ImpactedNode[]; emptyLabel: string }) {
  if (nodes.length === 0) {
    return <p className="text-xs text-fg-muted">{emptyLabel}</p>;
  }
  return (
    <ul className="flex flex-wrap gap-2">
      {nodes.map((node) => (
        <li
          key={node.id}
          className="rounded-md bg-surface-raised px-2 py-1 text-xs text-fg-secondary"
          title={node.node_type}
        >
          {node.name}
        </li>
      ))}
    </ul>
  );
}

function DeterministicPanel({ analysis }: { analysis: PullRequestAnalysis }) {
  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center gap-3">
        <RiskBadge level={analysis.risk.toLowerCase() as "high" | "medium" | "low"} />
        <span className="text-xs text-fg-muted">
          <StatusBadge label={analysis.risk} tone={RISK_TONE[analysis.risk]} />
        </span>
      </div>

      <div>
        <p className="mb-1 text-xs font-medium uppercase tracking-wide text-fg-muted">
          Directly impacted
        </p>
        <ImpactedNodeList
          nodes={analysis.directly_impacted_services}
          emptyLabel="No directly impacted components."
        />
      </div>

      <div>
        <p className="mb-1 text-xs font-medium uppercase tracking-wide text-fg-muted">
          Indirectly impacted (cross-repository)
        </p>
        <ImpactedNodeList
          nodes={analysis.indirectly_impacted_services}
          emptyLabel="No cross-repository impact detected."
        />
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <div>
          <p className="mb-1 text-xs font-medium uppercase tracking-wide text-fg-muted">APIs</p>
          <ImpactedNodeList nodes={analysis.impacted_apis} emptyLabel="None." />
        </div>
        <div>
          <p className="mb-1 text-xs font-medium uppercase tracking-wide text-fg-muted">
            Kafka topics
          </p>
          <ImpactedNodeList nodes={analysis.impacted_topics} emptyLabel="None." />
        </div>
        <div>
          <p className="mb-1 text-xs font-medium uppercase tracking-wide text-fg-muted">
            Libraries
          </p>
          <ImpactedNodeList nodes={analysis.impacted_libraries} emptyLabel="None." />
        </div>
      </div>

      {analysis.dependency_paths.length > 0 && (
        <div>
          <p className="mb-1 text-xs font-medium uppercase tracking-wide text-fg-muted">
            Dependency paths
          </p>
          <ul className="flex flex-col gap-1">
            {analysis.dependency_paths.map((path, i) => (
              <li key={i} className="text-xs text-fg-muted">
                {path.steps.map((step) => step.node_name).join(" → ")}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function AiAnalysisPanel({ ai }: { ai: AIAnalysis | AIAnalysisResult }) {
  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm text-fg-secondary">{ai.executive_summary}</p>

      {ai.breaking_changes.length > 0 && (
        <div>
          <p className="mb-1 text-xs font-medium uppercase tracking-wide text-fg-muted">
            Breaking changes
          </p>
          <ul className="flex flex-col gap-2">
            {ai.breaking_changes.map((bc, i) => (
              <li key={i} className="rounded-md bg-surface-raised p-2 text-xs">
                <span className="font-medium text-fg-secondary">{bc.component}</span> —{" "}
                {bc.description} <span className="text-fg-muted">({bc.severity})</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {ai.migration_advice.length > 0 && (
        <div>
          <p className="mb-1 text-xs font-medium uppercase tracking-wide text-fg-muted">
            Migration advice
          </p>
          <ul className="flex flex-col gap-2">
            {ai.migration_advice.map((advice, i) => (
              <li key={i} className="rounded-md bg-surface-raised p-2 text-xs">
                <span className="font-medium text-fg-secondary">{advice.component}</span> —{" "}
                {advice.advice} <span className="text-fg-muted">({advice.priority})</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {ai.suggested_reviewers.length > 0 && (
        <div>
          <p className="mb-1 text-xs font-medium uppercase tracking-wide text-fg-muted">
            Suggested reviewers
          </p>
          <ul className="flex flex-wrap gap-2">
            {ai.suggested_reviewers.map((reviewer, i) => (
              <li
                key={i}
                className="rounded-md bg-surface-raised px-2 py-1 text-xs text-fg-secondary"
                title={reviewer.reason}
              >
                {reviewer.reviewer}
              </li>
            ))}
          </ul>
        </div>
      )}

      {ai.regression_tests.length > 0 && (
        <div>
          <p className="mb-1 text-xs font-medium uppercase tracking-wide text-fg-muted">
            Suggested regression tests
          </p>
          <ul className="flex flex-col gap-2">
            {ai.regression_tests.map((test, i) => (
              <li key={i} className="rounded-md bg-surface-raised p-2 text-xs">
                <span className="font-medium text-fg-secondary">{test.component}</span> —{" "}
                {test.test_description}
              </li>
            ))}
          </ul>
        </div>
      )}

      <p className="text-xs text-fg-muted">
        Confidence:{" "}
        {Math.round(("confidence" in ai ? ai.confidence.score : ai.confidence_score) * 100)}%
      </p>
    </div>
  );
}

function ReleaseCoordinationPanel({
  plan,
}: {
  plan: AIAnalysisResult["release_coordination_plan"];
}) {
  return (
    <div className="flex flex-col gap-4">
      <p className="text-xs text-fg-muted">
        Regenerated fresh on every run — not persisted. Only shown for the AI analysis you just ran.
      </p>

      {plan.deployment_order.length > 0 && (
        <div>
          <p className="mb-1 text-xs font-medium uppercase tracking-wide text-fg-muted">
            Deployment order
          </p>
          <ol className="flex flex-col gap-2">
            {plan.deployment_order.map((step) => (
              <li key={step.order} className="rounded-md bg-surface-raised p-2 text-xs">
                <span className="font-medium text-fg-secondary">
                  {step.order}. {step.repository}
                </span>{" "}
                — {step.action} <span className="text-fg-muted">({step.reason})</span>
              </li>
            ))}
          </ol>
        </div>
      )}

      {plan.repositories_to_notify.length > 0 && (
        <div>
          <p className="mb-1 text-xs font-medium uppercase tracking-wide text-fg-muted">
            Repositories to notify
          </p>
          <ul className="flex flex-col gap-2">
            {plan.repositories_to_notify.map((entry, i) => (
              <li
                key={i}
                className="flex items-center gap-2 rounded-md bg-surface-raised p-2 text-xs"
              >
                <StatusBadge
                  label={entry.urgency}
                  tone={entry.urgency === "blocking" ? "danger" : "warning"}
                />
                <span className="font-medium text-fg-secondary">{entry.repository}</span>
                <span className="text-fg-muted">{entry.reason}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {plan.rollout_strategy && (
        <div>
          <p className="mb-1 text-xs font-medium uppercase tracking-wide text-fg-muted">
            Rollout strategy
          </p>
          <p className="text-xs text-fg-secondary">{plan.rollout_strategy}</p>
        </div>
      )}

      {plan.backward_compatibility_advice && (
        <div>
          <p className="mb-1 text-xs font-medium uppercase tracking-wide text-fg-muted">
            Backward compatibility
          </p>
          <p className="text-xs text-fg-secondary">{plan.backward_compatibility_advice}</p>
        </div>
      )}

      {plan.communication_summary && (
        <div>
          <p className="mb-1 text-xs font-medium uppercase tracking-wide text-fg-muted">
            Communication summary
          </p>
          <p className="text-xs text-fg-secondary">{plan.communication_summary}</p>
        </div>
      )}

      {plan.rollout_risks.length > 0 && (
        <div>
          <p className="mb-1 text-xs font-medium uppercase tracking-wide text-fg-muted">
            Rollout risks
          </p>
          <ul className="list-inside list-disc text-xs text-fg-secondary">
            {plan.rollout_risks.map((risk, i) => (
              <li key={i}>{risk}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

export function PullRequestDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { token } = useAuth();
  const { modelId } = useAiModel();
  const {
    pullRequests,
    isLoading: isLoadingPullRequests,
    error: pullRequestsError,
  } = usePullRequestsData();
  const pr = pullRequests.find((row) => row.id === id);

  const [deterministic, setDeterministic] = useState<PullRequestAnalysis | null>(null);
  const [aiAnalysis, setAiAnalysis] = useState<AIAnalysis | AIAnalysisResult | null>(null);
  const [releasePlan, setReleasePlan] = useState<
    AIAnalysisResult["release_coordination_plan"] | null
  >(null);
  const [isRunningDeterministic, setIsRunningDeterministic] = useState(false);
  const [isRunningAi, setIsRunningAi] = useState(false);
  const [isInvestigating, setIsInvestigating] = useState(false);
  const [isPublishing, setIsPublishing] = useState(false);
  const [isOpeningReport, setIsOpeningReport] = useState(false);
  const [publishedCommentUrl, setPublishedCommentUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [generatedWithModelId, setGeneratedWithModelId] = useState<AiModelId | null>(null);
  const [reasoningLog, setReasoningLog] = useState<ReasoningStep[] | null>(null);

  useEffect(() => {
    if (!token || !id) {
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const [existingDeterministic, existingAi] = await Promise.all([
          orNull(getDeterministicAnalysis(token, id)),
          orNull(getAiAnalysis(token, id)),
        ]);
        if (!cancelled) {
          if (existingDeterministic) setDeterministic(existingDeterministic);
          if (existingAi) setAiAnalysis(existingAi);
        }
      } catch (err) {
        // `orNull` already absorbs "not analyzed yet" (404) — anything
        // that reaches here is a genuine failure (expired token, network
        // error, 5xx). Previously unhandled: this fetch runs fire-and-
        // forget from a useEffect with no caller to catch it, so any
        // non-404 error became an unhandled promise rejection instead of
        // the same error banner every other action on this page shows.
        if (!cancelled) {
          setError(
            err instanceof Error ? err.message : "Failed to load existing analysis results."
          );
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [token, id]);

  async function handleRunDeterministic() {
    if (!token || !id) return;
    setIsRunningDeterministic(true);
    setError(null);
    try {
      setDeterministic(await runDeterministicAnalysis(token, id));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to run deterministic analysis.");
    } finally {
      setIsRunningDeterministic(false);
    }
  }

  async function handleRunAi() {
    if (!token || !id) return;
    setIsRunningAi(true);
    setError(null);
    try {
      const result = await runAiAnalysis(token, id, modelId);
      setAiAnalysis(result);
      setReleasePlan(result.release_coordination_plan);
      setGeneratedWithModelId(modelId);
      // Only the investigate flow produces a reasoning log - clear any
      // stale one from a prior "Investigate (Agent)" click.
      setReasoningLog(null);
      // A fresh analysis supersedes whatever was previously published.
      setPublishedCommentUrl(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to run AI analysis.");
    } finally {
      setIsRunningAi(false);
    }
  }

  async function handlePublishReview() {
    if (!token || !id) return;
    setIsPublishing(true);
    setError(null);
    try {
      const result = await publishReview(token, id);
      setPublishedCommentUrl(result.comment_url);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to publish review to GitHub.");
    } finally {
      setIsPublishing(false);
    }
  }

  async function handleOpenReport() {
    if (!token || !id) return;
    // Open the tab synchronously on the click so popup blockers don't
    // treat it as an unsolicited window.open() once the fetch resolves.
    const reportWindow = window.open("", "_blank");
    setIsOpeningReport(true);
    setError(null);
    try {
      const html = await getReviewReportHtml(token, id);
      const blobUrl = URL.createObjectURL(new Blob([html], { type: "text/html" }));
      if (reportWindow) {
        reportWindow.location.href = blobUrl;
      } else {
        setError("Pop-up blocked - allow pop-ups for this site to view the report.");
      }
    } catch (err) {
      reportWindow?.close();
      setError(err instanceof Error ? err.message : "Failed to load the visual report.");
    } finally {
      setIsOpeningReport(false);
    }
  }

  async function handleInvestigate() {
    if (!token || !id) return;
    setIsInvestigating(true);
    setError(null);
    try {
      const result = await investigatePullRequest(token, id, modelId);
      setAiAnalysis(result);
      setReleasePlan(result.release_coordination_plan);
      setGeneratedWithModelId(modelId);
      setReasoningLog(result.reasoning_log);
      setPublishedCommentUrl(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to run the investigation agent.");
    } finally {
      setIsInvestigating(false);
    }
  }

  if (!pr) {
    if (isLoadingPullRequests) {
      return <p className="text-sm text-fg-muted">Loading…</p>;
    }
    if (pullRequestsError) {
      return (
        <div className="flex flex-col gap-4">
          <Link
            to="/pull-requests"
            className="inline-flex items-center gap-1 text-sm text-fg-muted hover:text-fg-secondary"
          >
            ← Back to pull requests
          </Link>
          <div className="rounded-lg border border-danger-line/30 bg-danger-bg px-4 py-3 text-sm text-danger-fg">
            {pullRequestsError}
          </div>
        </div>
      );
    }
    // Loading finished with no error, and no row matches this id — a bad
    // link, a deleted PR, or a typo'd URL. Distinct from the loading state
    // above so this never looks like a spinner stuck forever.
    return (
      <div className="flex flex-col gap-4">
        <Link
          to="/pull-requests"
          className="inline-flex items-center gap-1 text-sm text-fg-muted hover:text-fg-secondary"
        >
          ← Back to pull requests
        </Link>
        <p className="text-sm text-fg-muted">Pull request not found.</p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h2 className="text-xl font-semibold text-fg">{pr.title}</h2>
        <p className="mt-1 text-sm text-fg-muted">
          <Link to={`/repositories/${pr.repositoryId}`} className="hover:underline">
            {pr.repositoryFullName}
          </Link>{" "}
          · #{pr.number} by {pr.authorLogin}
        </p>
      </div>

      {error && (
        <div className="rounded-lg border border-danger-line/30 bg-danger-bg px-4 py-3 text-sm text-danger-fg">
          {error}
        </div>
      )}

      <Card
        title="Deterministic analysis"
        description="Graph-based impact analysis - no AI involved"
        action={
          <button
            type="button"
            onClick={() => void handleRunDeterministic()}
            disabled={isRunningDeterministic}
            className="rounded-md bg-info-solid px-3 py-1.5 text-sm font-medium text-info-on-solid hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {isRunningDeterministic
              ? "Running…"
              : deterministic
                ? "Re-run analysis"
                : "Run analysis"}
          </button>
        }
      >
        {deterministic ? (
          <DeterministicPanel analysis={deterministic} />
        ) : (
          <p className="text-sm text-fg-muted">Not analyzed yet.</p>
        )}
      </Card>

      <Card
        title="AI analysis"
        description="Executive summary, breaking changes, migration advice, and suggested reviewers"
        action={
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => void handleRunAi()}
              disabled={isRunningAi || isInvestigating}
              className="rounded-md bg-info-solid px-3 py-1.5 text-sm font-medium text-info-on-solid hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {isRunningAi ? "Running…" : aiAnalysis ? "Re-run AI analysis" : "Run AI analysis"}
            </button>
            <button
              type="button"
              onClick={() => void handleInvestigate()}
              disabled={isRunningAi || isInvestigating}
              title="Runs the Change Investigation Agent - it decides which evidence to gather"
              className="rounded-md bg-accent-solid px-3 py-1.5 text-sm font-medium text-accent-on-solid hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {isInvestigating ? "Investigating…" : "Investigate (Agent)"}
            </button>
            <button
              type="button"
              onClick={() => void handlePublishReview()}
              disabled={isRunningAi || isInvestigating || isPublishing || !aiAnalysis}
              title="Publishes the AI analysis above as a comment on the GitHub pull request"
              className="rounded-md bg-success-solid px-3 py-1.5 text-sm font-medium text-success-on-solid hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {isPublishing
                ? "Publishing…"
                : publishedCommentUrl
                  ? "✓ Review published"
                  : "Publish Review"}
            </button>
            <button
              type="button"
              onClick={() => void handleOpenReport()}
              disabled={!aiAnalysis || isOpeningReport}
              title="Opens the full executive dashboard - score bars, filterable findings, per-file review cards"
              className="rounded-md border border-line-muted bg-surface-raised px-3 py-1.5 text-sm font-medium text-fg-secondary hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {isOpeningReport ? "Opening…" : "View Visual Report"}
            </button>
          </div>
        }
      >
        <div className="flex flex-col gap-4">
          <AiModelSelector />
          {aiAnalysis ? (
            <>
              {generatedWithModelId && (
                <div className="flex flex-wrap gap-x-6 gap-y-1 rounded-lg border border-line-muted bg-canvas px-3 py-2 text-xs">
                  <span className="text-fg-muted">
                    Generated using{" "}
                    <span className="font-medium text-fg-secondary">
                      {findAiModel(generatedWithModelId).label}
                    </span>
                  </span>
                  <span className="text-fg-muted">
                    Estimated cost{" "}
                    <span className="font-medium text-fg-secondary">
                      {findAiModel(generatedWithModelId).estimatedCost}
                    </span>
                  </span>
                  <span className="text-fg-muted">
                    Provider <span className="font-medium text-fg-secondary">OpenAI</span>
                  </span>
                </div>
              )}
              <AiAnalysisPanel ai={aiAnalysis} />
              {publishedCommentUrl && (
                <a
                  href={publishedCommentUrl}
                  target="_blank"
                  rel="noreferrer"
                  className="text-xs font-medium text-success-fg hover:underline"
                >
                  View published comment on GitHub →
                </a>
              )}
              {reasoningLog && <ReasoningLogPanel steps={reasoningLog} />}
            </>
          ) : (
            <p className="text-sm text-fg-muted">Not analyzed yet.</p>
          )}
        </div>
      </Card>

      <Card
        title="Release Coordination Plan"
        description="Deployment order and cross-repository notifications - AI-enriched, ephemeral"
      >
        {releasePlan ? (
          <ReleaseCoordinationPanel plan={releasePlan} />
        ) : (
          <p className="text-sm text-fg-muted">
            Run AI analysis above to generate a release coordination plan.
          </p>
        )}
      </Card>
    </div>
  );
}
