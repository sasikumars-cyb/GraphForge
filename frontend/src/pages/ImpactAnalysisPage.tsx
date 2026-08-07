import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Radar, Send, RotateCcw, History } from "lucide-react";
import { Card } from "../components/Card";
import { EvidencePanel } from "../components/EvidencePanel";
import { NodeDetailPanel } from "../components/architecture/NodeDetailPanel";
import { BlastRadiusGraph } from "../components/impact/BlastRadiusGraph";
import { ImpactedNodesTable } from "../components/impact/ImpactedNodesTable";
import { RunProgress } from "../components/agents/RunProgress";
import { RunStatusBadge } from "../components/agents/RunStatusBadge";
import { useAgentRun } from "../hooks/useAgentRun";
import { useAuth } from "../app/auth-context";
import { getBlastRadius } from "../lib/api/impact";
import { listTrackedRepositories } from "../lib/api/github";
import type { GraphNode } from "../types/graph";

const MAX_HOPS = 2;

/**
 * Impact Check — the Impact lens (ARCHITECTURE_EXPERIENCE_REDESIGN.md).
 * Visualization-first: selecting a repository immediately renders its
 * blast-radius graph (a fast, deterministic call — no LLM in the way of
 * the 5-10-second read), with the impacted-components table and node
 * detail panel as supporting evidence beside/beneath it. The LLM-narrated
 * executive summary (what this page used to lead with) is still here, but
 * demoted to an explicit, on-demand secondary action — appropriate for
 * "explain this in prose," not for the primary experience.
 */
export function ImpactAnalysisPage() {
  const { token } = useAuth();
  const [selectedRepoId, setSelectedRepoId] = useState("");
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null);
  const { run, isSubmitting, error: reportError, submit, reset } = useAgentRun();

  const repositoriesQuery = useQuery({
    queryKey: ["repositories"],
    queryFn: ({ signal }) => listTrackedRepositories(token as string, signal),
    enabled: token !== null,
  });
  const repositories = repositoriesQuery.data ?? [];

  const blastRadiusQuery = useQuery({
    queryKey: ["blast-radius", selectedRepoId, MAX_HOPS],
    queryFn: ({ signal }) =>
      getBlastRadius(token as string, selectedRepoId, { maxHops: MAX_HOPS }, signal),
    enabled: token !== null && selectedRepoId !== "",
  });

  function selectRepository(id: string) {
    setSelectedRepoId(id);
    setSelectedNode(null);
    reset();
  }

  function handleGenerateReport(e: FormEvent) {
    e.preventDefault();
    if (!selectedRepoId) return;
    submit({ subject_reference: `repo:${selectedRepoId}`, goal: "analyze_impact_analysis" });
  }

  const hasReport =
    run && (run.status === "completed" || run.status === "partial" || run.status === "failed");

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="rounded-lg bg-cat-6-bg p-2 ring-1 ring-inset ring-cat-6-line/30">
            <Radar className="h-5 w-5 text-cat-6-fg" aria-hidden="true" />
          </div>
          <div>
            <h1 className="text-xl font-semibold text-fg">Impact Check</h1>
            <p className="text-sm text-fg-muted">
              A repository&apos;s blast radius, by hop distance from its own root — how directly
              (or distantly) a change ripples outward.
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
        <label htmlFor="impact-repo" className="block text-sm font-medium text-fg-secondary">
          Repository
        </label>
        <select
          id="impact-repo"
          value={selectedRepoId}
          onChange={(e) => selectRepository(e.target.value)}
          disabled={repositoriesQuery.isPending}
          className="mt-2 w-full rounded-lg border border-line bg-surface-raised px-4 py-3 text-sm text-fg disabled:opacity-50"
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

      {selectedRepoId && (
        <Card
          title="Blast radius"
          description={`Within ${MAX_HOPS} hops of the repository's own root node.`}
        >
          {blastRadiusQuery.isPending ? (
            <div className="flex flex-col gap-3" aria-busy="true" aria-label="Loading blast radius">
              <div className="h-[clamp(24rem,70vh,45rem)] animate-pulse rounded-xl bg-surface" />
            </div>
          ) : blastRadiusQuery.isError ? (
            <div className="rounded-lg border border-danger-line/30 bg-danger-bg px-4 py-3 text-sm text-danger-fg">
              Failed to compute the blast radius.
            </div>
          ) : blastRadiusQuery.data.graph.nodes.length === 0 ? (
            <p className="py-8 text-center text-sm text-fg-muted">
              This repository hasn&apos;t been indexed yet, or has no graph to compute impact from.
            </p>
          ) : (
            <div className="flex flex-col gap-4">
              <div className="flex overflow-hidden rounded-xl border border-line">
                <div className="min-w-0 flex-1">
                  <BlastRadiusGraph
                    graph={blastRadiusQuery.data.graph}
                    seedNodeId={blastRadiusQuery.data.seed_node_id}
                    onNodeSelect={setSelectedNode}
                    selectedNodeId={selectedNode?.id ?? null}
                  />
                </div>
                {selectedNode && (
                  <NodeDetailPanel node={selectedNode} onClose={() => setSelectedNode(null)} />
                )}
              </div>
              <ImpactedNodesTable
                nodes={blastRadiusQuery.data.graph.nodes}
                seedNodeId={blastRadiusQuery.data.seed_node_id}
                selectedNodeId={selectedNode?.id ?? null}
                onSelect={setSelectedNode}
              />
            </div>
          )}
        </Card>
      )}

      {selectedRepoId && !hasReport && (
        <Card
          title="Detailed report"
          description="A narrated summary — risk assessment, confidence per relationship. Slower; generated on demand."
        >
          <form onSubmit={handleGenerateReport} className="flex items-center gap-3">
            <button
              type="submit"
              disabled={isSubmitting}
              className="inline-flex items-center gap-2 rounded-lg bg-cat-6-solid px-4 py-2 text-sm font-medium text-cat-6-on-solid transition-colors hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <Send className="h-4 w-4" aria-hidden="true" />
              {isSubmitting ? "Generating…" : "Generate detailed report"}
            </button>
            {isSubmitting && (
              <span className="text-xs text-fg-muted">This may take up to a minute.</span>
            )}
          </form>
          {run && !hasReport && (
            <div className="mt-4">
              <RunProgress status={run.status} error={run.error_message} />
            </div>
          )}
          {reportError && (
            <div className="mt-4 rounded-lg border border-danger-line/30 bg-danger-bg px-4 py-3 text-sm text-danger-fg">
              {reportError}
            </div>
          )}
        </Card>
      )}

      {hasReport && <ImpactReportView run={run} onNewReport={reset} />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Detailed report — the LLM-narrated executive summary, demoted to
// supporting content generated on demand (see the page's own docstring).
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
  onNewReport,
}: {
  run: NonNullable<ReturnType<typeof useAgentRun>["run"]>;
  onNewReport: () => void;
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
          onClick={onNewReport}
          className="inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium text-fg-muted ring-1 ring-inset ring-line transition-colors hover:bg-surface-raised hover:text-fg-secondary"
        >
          <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />
          New Report
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
