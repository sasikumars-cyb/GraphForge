import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Layers, Send, RotateCcw, History } from "lucide-react";
import { Card } from "../components/Card";
import { EvidencePanel } from "../components/EvidencePanel";
import { DependencyTreeExplorer } from "../components/dependency/DependencyTreeExplorer";
import { RunProgress } from "../components/agents/RunProgress";
import { RunStatusBadge } from "../components/agents/RunStatusBadge";
import { useAgentRun } from "../hooks/useAgentRun";
import { useAuth } from "../app/auth-context";
import { listTrackedRepositories } from "../lib/api/github";

/**
 * The Dependency lens (ARCHITECTURE_EXPERIENCE_REDESIGN.md) — leads with
 * the expandable dependency tree (`DependencyTreeExplorer`), the same
 * "visualization first, LLM narration on demand" shape Impact Check
 * established for the Impact lens. The `analyze_dependency_query` agent
 * flow this page used to lead with (a ranked-list-only report) is kept,
 * demoted to an explicit, on-demand "Detailed report" action — appropriate
 * for "explain this in prose, with confidence per relationship," not for
 * the primary five-second read.
 */
export function DependencyQueryPage() {
  const { token } = useAuth();
  const [selectedRepoId, setSelectedRepoId] = useState("");
  const { run, isSubmitting, error, submit, reset } = useAgentRun();

  const repositoriesQuery = useQuery({
    queryKey: ["repositories"],
    queryFn: ({ signal }) => listTrackedRepositories(token as string, signal),
    enabled: token !== null,
  });
  const repositories = repositoriesQuery.data ?? [];
  const selectedRepo = repositories.find((r) => r.id === selectedRepoId);

  function selectRepository(id: string) {
    setSelectedRepoId(id);
    reset();
  }

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (!selectedRepoId) return;
    submit({
      subject_reference: `repo:${selectedRepoId}`,
      goal: "analyze_dependency_query",
    });
  };

  const hasResult =
    run && (run.status === "completed" || run.status === "partial" || run.status === "failed");

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="rounded-lg bg-cat-5-bg p-2 ring-1 ring-inset ring-cat-5-line/30">
            <Layers className="h-5 w-5 text-cat-5-fg" aria-hidden="true" />
          </div>
          <div>
            <h1 className="text-xl font-semibold text-fg">Dependency</h1>
            <p className="text-sm text-fg-muted">
              What a repository depends on, and what depends on it — an expandable tree, not a
              list to read.
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

      <Card>
        <label htmlFor="dependency-repo" className="block text-sm font-medium text-fg-secondary">
          Repository
        </label>
        <select
          id="dependency-repo"
          value={selectedRepoId}
          onChange={(e) => selectRepository(e.target.value)}
          disabled={repositoriesQuery.isPending}
          className="mt-2 w-full rounded-lg border border-line bg-surface-raised px-4 py-3 text-sm text-fg disabled:opacity-50"
          aria-required="true"
        >
          <option value="">
            {repositoriesQuery.isPending ? "Loading repositories…" : "Select a repository…"}
          </option>
          {repositories.map((repo) => (
            <option key={repo.id} value={repo.id}>
              {repo.full_name}
            </option>
          ))}
        </select>
        {repositoriesQuery.isSuccess && repositories.length === 0 && (
          <p className="mt-1 text-xs text-fg-muted">
            No tracked repositories yet — add one under Repositories first.
          </p>
        )}
      </Card>

      {selectedRepo && (
        <DependencyTreeExplorer
          key={selectedRepo.id}
          repositoryId={selectedRepo.id}
          repositoryName={selectedRepo.full_name}
        />
      )}

      {selectedRepoId && !hasResult && (
        <Card
          title="Detailed report"
          description="A narrated summary — verified vs. candidate relationships, confidence per edge. Slower; generated on demand."
        >
          <form onSubmit={handleSubmit} className="flex items-center gap-3">
            <button
              type="submit"
              disabled={isSubmitting}
              className="inline-flex items-center gap-2 rounded-lg bg-cat-5-solid px-4 py-2 text-sm font-medium text-cat-5-on-solid transition-colors hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <Send className="h-4 w-4" aria-hidden="true" />
              {isSubmitting ? "Analyzing…" : "Generate detailed report"}
            </button>
            {isSubmitting && (
              <span className="text-xs text-fg-muted">This may take up to a minute.</span>
            )}
          </form>
          {run && !hasResult && (
            <div className="mt-4">
              <RunProgress status={run.status} error={run.error_message} />
            </div>
          )}
          {error && (
            <div className="mt-4 rounded-lg border border-danger-line/30 bg-danger-bg px-4 py-3 text-sm text-danger-fg">
              {error}
            </div>
          )}
        </Card>
      )}

      {hasResult && <DependencyReportView run={run} onNewAnalysis={reset} />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Detailed report — the LLM-narrated summary, demoted to supporting
// content generated on demand (see the page's own docstring).
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

function DependencyReportView({
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
  const directDependencies = (result?.direct_dependencies as string[]) ?? [];
  const directDependenciesSummary = (result?.direct_dependencies_summary as string) ?? "";
  const downstreamConsumers = (result?.downstream_consumers as string[]) ?? [];
  const downstreamConsumersSummary = (result?.downstream_consumers_summary as string) ?? "";
  const downstreamConsumersCaveat = (result?.downstream_consumers_caveat as string) ?? "";
  const verifiedRelationships = (result?.verified_relationships as string[]) ?? [];
  const candidateRelationships = (result?.candidate_relationships as string[]) ?? [];
  const confidenceBreakdown = (result?.confidence_breakdown as Record<string, number>) ?? {
    high: 0,
    medium: 0,
    low: 0,
  };
  const architecturalNotes = (result?.architectural_notes as string[]) ?? [];

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

      <Card
        title="Direct Dependencies"
        description={directDependenciesSummary || `${directDependencies.length} found`}
      >
        {directDependencies.length === 0 ? (
          <p className="text-sm text-fg-muted">No dependencies found.</p>
        ) : (
          <ul className="space-y-1 text-sm text-fg-secondary" role="list">
            {directDependencies.map((dep, i) => (
              <li key={i} className="font-mono text-xs">
                {dep}
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card
        title="Downstream Consumers"
        description={downstreamConsumersSummary || `${downstreamConsumers.length} found`}
      >
        {downstreamConsumers.length === 0 ? (
          <p className="text-sm text-fg-muted">No downstream consumers found.</p>
        ) : (
          <ul className="space-y-1 text-sm text-fg-secondary" role="list">
            {downstreamConsumers.map((consumer, i) => (
              <li key={i} className="font-mono text-xs">
                {consumer}
              </li>
            ))}
          </ul>
        )}
        {downstreamConsumersCaveat && (
          <p className="mt-3 border-t border-line pt-3 text-xs text-fg-muted">
            {downstreamConsumersCaveat}
          </p>
        )}
      </Card>

      <ListSection title="Verified Relationships" items={verifiedRelationships} />
      <ListSection title="Candidate Relationships" items={candidateRelationships} />

      <Card title="Confidence Breakdown">
        <dl className="grid grid-cols-3 gap-x-6 gap-y-3 text-sm">
          <div>
            <dt className="text-xs text-success-fg">High confidence</dt>
            <dd className="text-fg-secondary">{confidenceBreakdown.high}</dd>
          </div>
          <div>
            <dt className="text-xs text-warning-fg">Medium confidence</dt>
            <dd className="text-fg-secondary">{confidenceBreakdown.medium}</dd>
          </div>
          <div>
            <dt className="text-xs text-danger-fg">Low confidence</dt>
            <dd className="text-fg-secondary">{confidenceBreakdown.low}</dd>
          </div>
        </dl>
      </Card>

      <ListSection title="Architectural Notes" items={architecturalNotes} />

      <EvidencePanel evidence={evidence} />
    </div>
  );
}
