import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Card } from "../components/Card";
import {
  DependencyGraph,
  RepositoryOverviewGraph,
  type RepositorySummary,
} from "../components/graph/DependencyGraph";
import { legendLabelsFor, resolveLabelColors } from "../components/graph/graphLabels";
import { useAuth } from "../app/auth-context";
import { listTrackedRepositories } from "../lib/api/github";
import {
  getAllCrossRepositoryLinks,
  getCrossRepositoryLinks,
  getLatestIndexingJob,
  getRepositoryGraph,
} from "../lib/api/repositories";
import { buildRepositoryDependencyEdges, mergeCrossRepositoryLinks } from "../lib/graph/mergeGraphs";
import { summarizeRepositoryCounts } from "../lib/indexingSummary";
import type { TrackedRepository } from "../types/github";
import type { CrossRepositoryLink, Graph } from "../types/graph";

/**
 * Descriptions for every relationship the indexer can write (see backend
 * `app/indexer/graph/builder.py`). Only the types present in the loaded
 * graph are rendered — see `edgeLegendFor` — so this stays a lookup table,
 * never the list itself. Wordings are language-neutral on purpose: the same
 * `DEPENDS_ON` edge carries a Maven artifact for Java and a pip package for
 * Python, and the previous "Depends on a Maven artifact" was simply wrong
 * for every Python repository.
 */
const EDGE_DESCRIPTIONS: Record<string, string> = {
  CONTAINS: "Contains a component or dependency",
  EXPOSES: "Exposes an endpoint",
  CALLS: "Calls another component or remote endpoint",
  IMPORTS: "Imports another module",
  INHERITS_FROM: "Inherits from a base class",
  DEPENDS_ON: "Depends on an external package",
  PRODUCES_TO: "Publishes to a messaging topic",
  CONSUMES_FROM: "Consumes from a messaging topic",
  READS_FROM: "Reads from a table or dataset",
  WRITES_TO: "Writes to a table or dataset",
};

/** Relationship types actually present in this graph, most frequent first. */
function edgeLegendFor(edges: { type: string }[]): { type: string; description: string }[] {
  const counts = new Map<string, number>();
  for (const edge of edges) counts.set(edge.type, (counts.get(edge.type) ?? 0) + 1);
  return [...counts.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .map(([type]) => ({ type, description: EDGE_DESCRIPTIONS[type] ?? "Relationship" }));
}

export function ArchitecturePage() {
  const { token } = useAuth();
  const [searchParams] = useSearchParams();
  const [repositories, setRepositories] = useState<TrackedRepository[]>([]);
  // Full node/edge graphs are only ever fetched lazily, per repository, the
  // first time that repository is expanded - never eagerly for the whole
  // org, so opening this page stays cheap regardless of how many
  // repositories are tracked.
  const [graphsByRepoId, setGraphsByRepoId] = useState<Record<string, Graph>>({});
  const [summariesByRepoId, setSummariesByRepoId] = useState<
    Record<string, Record<string, number> | null>
  >({});
  const [selectedRepoId, setSelectedRepoId] = useState<string>(
    searchParams.get("repository") ?? "all",
  );
  const [isLoading, setIsLoading] = useState(true);
  const [isLoadingSelectedGraph, setIsLoadingSelectedGraph] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Cached once for the overview - never refetched on hover.
  const [allLinks, setAllLinks] = useState<CrossRepositoryLink[] | null>(null);

  // Cheap initial load: repository list + each repository's lightweight
  // indexing summary counts (not its full graph) - enough to render one
  // overview card per repository.
  useEffect(() => {
    if (!token) {
      return;
    }
    let cancelled = false;

    async function load() {
      try {
        const repos = await listTrackedRepositories(token!);
        const summaries = await Promise.all(
          repos.map((repo) =>
            getLatestIndexingJob(token!, repo.id)
              .then((job) => job.result_summary)
              .catch(() => null),
          ),
        );
        if (!cancelled) {
          setRepositories(repos);
          setSummariesByRepoId(Object.fromEntries(repos.map((repo, i) => [repo.id, summaries[i]])));
          setIsLoading(false);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Failed to load repositories.");
          setIsLoading(false);
        }
      }
    }

    void load();
    return () => {
      cancelled = true;
    };
  }, [token]);

  // Overview repository-to-repository edges: one request for every tracked
  // repository's cross-repository links at once (one Neo4j relationship
  // query server-side), fetched once and cached - never per hover, and no
  // longer one HTTP request per repository.
  useEffect(() => {
    if (!token || repositories.length === 0 || allLinks !== null) {
      return;
    }
    let cancelled = false;

    async function loadLinks() {
      const links = await getAllCrossRepositoryLinks(token!).catch(() => []);
      if (!cancelled) {
        setAllLinks(links);
      }
    }

    void loadLinks();
    return () => {
      cancelled = true;
    };
  }, [token, repositories, allLinks]);

  // Lazy load: only fetch a repository's own graph once it's expanded, plus
  // one lightweight `/cross-repository-links` call to discover which other
  // repositories it's connected to - never any other repository's full
  // graph. Exactly two requests per expand.
  useEffect(() => {
    if (!token || selectedRepoId === "all" || graphsByRepoId[selectedRepoId]) {
      return;
    }
    let cancelled = false;

    async function loadSelected() {
      setIsLoadingSelectedGraph(true);
      const [ownGraph, links] = await Promise.all([
        getRepositoryGraph(token!, selectedRepoId),
        getCrossRepositoryLinks(token!, selectedRepoId).catch(() => []),
      ]);
      if (cancelled) return;
      setGraphsByRepoId((prev) => ({
        ...prev,
        [selectedRepoId]: mergeCrossRepositoryLinks(ownGraph, links),
      }));
      setIsLoadingSelectedGraph(false);
    }

    void loadSelected();
    return () => {
      cancelled = true;
    };
  }, [token, selectedRepoId, graphsByRepoId]);

  const graph: Graph | null = selectedRepoId === "all" ? null : (graphsByRepoId[selectedRepoId] ?? null);
  const repositoryNameById = Object.fromEntries(repositories.map((r) => [r.id, r.full_name]));
  const repositorySummaries: RepositorySummary[] = repositories.map((r) => ({
    id: r.id,
    name: r.full_name,
    ...summarizeRepositoryCounts(summariesByRepoId[r.id]),
  }));
  const repositoryDependencyEdges = allLinks ? buildRepositoryDependencyEdges(allLinks) : [];
  const legendNodeLabels = graph ? legendLabelsFor(graph.nodes) : [];
  const legendEdges = graph ? edgeLegendFor(graph.edges) : [];

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h2 className="text-xl font-semibold text-slate-50">Architecture</h2>
          <p className="mt-1 text-sm text-slate-400">
            The dependency graph generated from indexed repositories, showing relationships
            between repositories, modules, services, APIs, data stores, messaging systems, and
            other software components.
          </p>
        </div>
        {repositories.length > 0 && (
          <label className="flex flex-col gap-1 text-xs text-slate-400">
            Repository
            <select
              value={selectedRepoId}
              onChange={(e) => setSelectedRepoId(e.target.value)}
              className="rounded-md border border-slate-700 bg-slate-900 px-3 py-1.5 text-sm text-slate-100"
            >
              <option value="all">All repositories (merged)</option>
              {repositories.map((repo) => (
                <option key={repo.id} value={repo.id}>
                  {repo.full_name}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>

      {error && (
        <div className="rounded-lg border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-300">
          {error}
        </div>
      )}

      <Card
        title="Dependency graph"
        description={
          selectedRepoId === "all"
            ? "One summary card per repository - select or expand a repository to load its detailed dependency graph on demand."
            : "This repository's own dependencies, plus any other repositories it shares a component with (inbound and outbound)."
        }
        action={
          selectedRepoId !== "all" && (
            <button
              type="button"
              onClick={() => setSelectedRepoId("all")}
              className="rounded-md border border-slate-700 px-3 py-1.5 text-sm text-slate-200 hover:border-slate-500"
            >
              ← Back to overview
            </button>
          )
        }
      >
        {isLoading ? (
          <div className="flex min-h-48 items-center justify-center text-sm text-slate-500">
            Loading repositories…
          </div>
        ) : repositories.length === 0 ? (
          <div className="flex min-h-48 flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-slate-700 bg-slate-950/50 text-slate-500">
            <p className="text-sm">No repositories tracked yet.</p>
          </div>
        ) : selectedRepoId === "all" ? (
          <RepositoryOverviewGraph
            repositories={repositorySummaries}
            edges={repositoryDependencyEdges}
            onExpand={(repoId) => setSelectedRepoId(repoId)}
          />
        ) : isLoadingSelectedGraph || !graph ? (
          <div className="flex min-h-48 items-center justify-center text-sm text-slate-500">
            Loading this repository's graph…
          </div>
        ) : graph.nodes.length === 0 ? (
          <div className="flex min-h-48 flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-slate-700 bg-slate-950/50 text-slate-500">
            <p className="text-sm">No graph data yet - index this repository first.</p>
          </div>
        ) : (
          <DependencyGraph graph={graph} repositoryNameById={repositoryNameById} />
        )}
      </Card>

      {/* Legend is built from the graph currently loaded, not a fixed list:
          it previously advertised six Java/Spring node types and six edge
          types regardless of what was indexed, so a Python repository got a
          legend describing a system it had none of — and no entry for the
          Module/Class/Function nodes it actually contained. Only rendered
          once a graph is loaded, since there is nothing to describe until
          then. */}
      {legendNodeLabels.length > 0 && (
        <Card title="Legend" description="Node and relationship types in this graph">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div>
              <p className="mb-2 text-xs font-medium uppercase tracking-wide text-slate-500">
                Nodes
              </p>
              <ul className="flex flex-col gap-1.5 text-sm text-slate-300">
                {legendNodeLabels.map((label) => {
                  const colors = resolveLabelColors(label);
                  return (
                    <li key={label} className="flex items-center gap-2">
                      <span
                        className="h-3 w-3 rounded-sm"
                        style={{
                          background: colors.background,
                          border: `1px solid ${colors.border}`,
                        }}
                      />
                      {label}
                    </li>
                  );
                })}
              </ul>
            </div>
            <div>
              <p className="mb-2 text-xs font-medium uppercase tracking-wide text-slate-500">
                Relationships
              </p>
              <ul className="flex flex-col gap-1.5 text-sm text-slate-300">
                {legendEdges.map(({ type, description }) => (
                  <li key={type}>
                    <span className="font-mono text-xs text-slate-400">{type}</span> — {description}
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </Card>
      )}
    </div>
  );
}
