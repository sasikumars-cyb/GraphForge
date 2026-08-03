import { useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { Radar, Send, RotateCcw, History } from "lucide-react";
import { Card } from "../components/Card";
import { EvidencePanel } from "../components/EvidencePanel";
import { RunProgress } from "../components/agents/RunProgress";
import { RunStatusBadge } from "../components/agents/RunStatusBadge";
import { useAgentRun } from "../hooks/useAgentRun";
import { useAuth } from "../app/auth-context";
import { listTrackedRepositories } from "../lib/api/github";
import type { TrackedRepository } from "../types/github";

export function ImpactAnalysisPage() {
  const { token } = useAuth();
  const [repositories, setRepositories] = useState<TrackedRepository[]>([]);
  const [selectedRepoId, setSelectedRepoId] = useState("");
  const [loadingRepos, setLoadingRepos] = useState(true);
  const [reposError, setReposError] = useState<string | null>(null);
  const { run, isSubmitting, error, submit, reset } = useAgentRun();

  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    listTrackedRepositories(token)
      .then((repos) => {
        if (!cancelled) setRepositories(repos);
      })
      .catch((err) => {
        if (!cancelled) {
          setReposError(err instanceof Error ? err.message : "Could not load repositories.");
        }
      })
      .finally(() => {
        if (!cancelled) setLoadingRepos(false);
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (!selectedRepoId) return;
    submit({
      subject_reference: `repo:${selectedRepoId}`,
      goal: "analyze_impact_analysis",
    });
  };

  const hasResult =
    run && (run.status === "completed" || run.status === "partial" || run.status === "failed");

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="rounded-lg bg-cat-6-bg p-2 ring-1 ring-inset ring-cat-6-line/30">
            <Radar className="h-5 w-5 text-cat-6-fg" aria-hidden="true" />
          </div>
          <div>
            <h2 className="text-xl font-semibold text-fg">Impact Analysis</h2>
            <p className="text-sm text-fg-muted">
              Compute a repository&apos;s blast radius — which repositories, APIs, databases, and
              queues it impacts, and how confident each relationship is. Read-only.
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

      {!hasResult && (
        <Card>
          <form onSubmit={handleSubmit} className="flex flex-col gap-4">
            <div>
              <label
                htmlFor="impact-analysis-repo"
                className="block text-sm font-medium text-fg-secondary"
              >
                Repository
              </label>
              {reposError ? (
                <p className="mt-2 text-xs text-danger-fg">{reposError}</p>
              ) : (
                <select
                  id="impact-analysis-repo"
                  value={selectedRepoId}
                  onChange={(e) => setSelectedRepoId(e.target.value)}
                  disabled={isSubmitting || loadingRepos}
                  className="mt-2 w-full rounded-lg border border-line bg-surface-raised px-4 py-3 text-sm text-fg disabled:opacity-50"
                  aria-required="true"
                >
                  <option value="">
                    {loadingRepos ? "Loading repositories…" : "Select a repository…"}
                  </option>
                  {repositories.map((repo) => (
                    <option key={repo.id} value={repo.id}>
                      {repo.full_name}
                    </option>
                  ))}
                </select>
              )}
              {!loadingRepos && !reposError && repositories.length === 0 && (
                <p className="mt-1 text-xs text-fg-muted">
                  No tracked repositories yet — add one under Repositories first.
                </p>
              )}
            </div>

            <div className="flex items-center gap-3">
              <button
                type="submit"
                disabled={isSubmitting || !selectedRepoId}
                className="inline-flex items-center gap-2 rounded-lg bg-cat-6-solid px-4 py-2 text-sm font-medium text-cat-6-on-solid transition-colors hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50"
                aria-label="Run impact analysis"
              >
                <Send className="h-4 w-4" aria-hidden="true" />
                {isSubmitting ? "Analyzing…" : "Run Impact Analysis"}
              </button>
              {isSubmitting && (
                <span className="text-xs text-fg-muted">This may take up to a minute.</span>
              )}
            </div>
          </form>
        </Card>
      )}

      {run && !hasResult && (
        <Card>
          <RunProgress status={run.status} error={run.error_message} />
        </Card>
      )}

      {error && (
        <div className="rounded-lg border border-danger-line/30 bg-danger-bg px-4 py-3 text-sm text-danger-fg">
          {error}
          <button
            type="button"
            onClick={reset}
            className="ml-3 text-danger-fg underline hover:text-danger-fg"
          >
            Try again
          </button>
        </div>
      )}

      {hasResult && <ImpactReportView run={run} onNewAnalysis={reset} />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Report sub-component
// ---------------------------------------------------------------------------

function ListSection({ title, items }: { title: string; items: string[] }) {
  if (items.length === 0) return null;
  return (
    <Card title={title} description={`${items.length} found`}>
      <ul className="list-inside list-disc space-y-1 text-sm text-fg-secondary" role="list">
        {items.map((item, i) => (
          <li key={i} className="font-mono text-xs">
            {item}
          </li>
        ))}
      </ul>
    </Card>
  );
}

function ImpactReportView({
  run,
  onNewAnalysis,
}: {
  run: NonNullable<ReturnType<typeof useAgentRun>["run"]>;
  onNewAnalysis: () => void;
}) {
  const step = run.steps[0];
  const result = step?.result as Record<string, unknown> | undefined;
  const evidence = step?.evidence ?? [];

  const executiveSummary = (result?.executive_summary as string) ?? "";
  const blastRadiusOverview = (result?.blast_radius_overview as string) ?? "";
  const directlyImpactedRepositories = (result?.directly_impacted_repositories as string[]) ?? [];
  const indirectlyImpactedApis = (result?.indirectly_impacted_apis as string[]) ?? [];
  const indirectImpactSummary = (result?.indirect_impact_summary as string) ?? "";
  const highRiskComponents = (result?.high_risk_components as string[]) ?? [];
  const confidenceSummary = (result?.confidence_summary as Record<string, number>) ?? {
    high: 0,
    medium: 0,
    low: 0,
  };
  const riskSummary = (result?.risk_summary as string) ?? "";

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <RunStatusBadge status={run.status} />
        <button
          type="button"
          onClick={onNewAnalysis}
          className="inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium text-fg-muted ring-1 ring-inset ring-line transition-colors hover:bg-surface-raised hover:text-fg-secondary"
        >
          <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />
          New Analysis
        </button>
      </div>

      {run.status === "failed" && run.error_message && (
        <div className="rounded-lg border border-danger-line/30 bg-danger-bg px-4 py-3 text-sm text-danger-fg">
          <strong>Error:</strong> {run.error_message}
        </div>
      )}

      <Card title="Executive Summary">
        <p className="text-sm text-fg-secondary">{executiveSummary || "No summary available."}</p>
      </Card>

      <Card title="Blast Radius Overview" description={blastRadiusOverview || undefined}>
        <dl className="grid grid-cols-2 gap-x-6 gap-y-3 text-sm sm:grid-cols-4">
          <div>
            <dt className="text-xs text-fg-muted">Repositories</dt>
            <dd className="text-fg-secondary">{directlyImpactedRepositories.length}</dd>
          </div>
          <div>
            <dt className="text-xs text-fg-muted">APIs</dt>
            <dd className="text-fg-secondary">{indirectlyImpactedApis.length}</dd>
          </div>
          <div>
            <dt className="text-xs text-fg-muted">High-risk components</dt>
            <dd className="text-fg-secondary">{highRiskComponents.length}</dd>
          </div>
          <div>
            <dt className="text-xs text-fg-muted">Relationships</dt>
            <dd className="text-fg-secondary">
              {confidenceSummary.high + confidenceSummary.medium + confidenceSummary.low}
            </dd>
          </div>
        </dl>
      </Card>

      <ListSection title="Directly Impacted Repositories" items={directlyImpactedRepositories} />

      {(indirectlyImpactedApis.length > 0 || indirectImpactSummary) && (
        <Card
          title="Indirectly Impacted APIs"
          description={indirectImpactSummary || `${indirectlyImpactedApis.length} found`}
        >
          {indirectlyImpactedApis.length === 0 ? (
            <p className="text-sm text-fg-muted">No indirectly impacted APIs found.</p>
          ) : (
            <ul className="space-y-1 text-sm text-fg-secondary" role="list">
              {indirectlyImpactedApis.map((api, i) => (
                <li key={i} className="font-mono text-xs">
                  {api}
                </li>
              ))}
            </ul>
          )}
        </Card>
      )}

      <ListSection title="High Risk Components" items={highRiskComponents} />

      <Card title="Confidence Summary">
        <dl className="grid grid-cols-3 gap-x-6 gap-y-3 text-sm">
          <div>
            <dt className="text-xs text-success-fg">High confidence</dt>
            <dd className="text-fg-secondary">{confidenceSummary.high}</dd>
          </div>
          <div>
            <dt className="text-xs text-warning-fg">Medium confidence</dt>
            <dd className="text-fg-secondary">{confidenceSummary.medium}</dd>
          </div>
          <div>
            <dt className="text-xs text-danger-fg">Low confidence</dt>
            <dd className="text-fg-secondary">{confidenceSummary.low}</dd>
          </div>
        </dl>
      </Card>

      <Card title="Risk Summary">
        <p className="text-sm text-fg-secondary">{riskSummary || "No risk summary available."}</p>
      </Card>

      <EvidencePanel evidence={evidence} />
    </div>
  );
}
