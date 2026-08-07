import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useQueries, useQuery } from "@tanstack/react-query";
import { Card } from "../components/Card";
import { EmptyState, SampleGraph } from "../components/EmptyState";
import {
  DependencyGraph,
  RepositoryOverviewGraph,
  type RepositorySummary,
} from "../components/graph/DependencyGraph";
import { legendLabelsFor, resolveLabelColors } from "../components/graph/graphLabels";
import { useAuth } from "../app/auth-context";
import { listTrackedRepositories } from "../lib/api/github";
import {
  getAllCrossRepositoryEdges,
  getAllCrossRepositoryLinks,
  getCrossRepositoryLinks,
  getLatestIndexingJob,
  getRepositoryGraph,
} from "../lib/api/repositories";
import {
  buildRepositoryDependencyEdges,
  buildStructuralDependencyEdges,
  mergeCrossRepositoryLinks,
  mergeRepositoryDependencyEdges,
} from "../lib/graph/mergeGraphs";
import { summarizeRepositoryCounts } from "../lib/indexingSummary";
import type { CrossRepositoryLink } from "../types/graph";

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

/** Stable key for a node-type filter combination, used as part of the
 * TanStack Query key for a repository's graph — a filtered view caches
 * separately from the unfiltered graph rather than overwriting it, so
 * toggling a filter off doesn't force a refetch of what was already
 * loaded. `null` (no filter) and the unfiltered fetch below intentionally
 * share the same key, so they dedupe as one cached entry rather than two. */
function nodeTypeFilterKey(nodeTypes: string[] | null): string {
  if (!nodeTypes || nodeTypes.length === 0) return "__all__";
  return [...nodeTypes].sort().join(",");
}

// KAN-37 — the manual `graphsByRepoId` LRU cache (capped at 5 entries,
// evicted oldest-first — see the Architecture Page Scale Redesign doc's
// audit, "every expanded repository's full graph stays resident in memory
// for the session") is gone: TanStack Query now owns this cache. A
// repository's graph query becomes inactive once another repository is
// selected and is garbage-collected `gcTime` after that — time-based
// rather than count-based, but the same intent (a large graph shouldn't
// stay resident forever once it's no longer being looked at).
const GRAPH_QUERY_GC_TIME_MS = 5 * 60_000;

export function ArchitecturePage() {
  const { token } = useAuth();
  const [searchParams] = useSearchParams();
  const [selectedRepoId, setSelectedRepoId] = useState<string>(
    searchParams.get("repository") ?? "all",
  );
  // A filter chosen for one repository's node types has no meaning once a
  // different repository is selected — reset alongside `selectedRepoId`
  // rather than in a separate effect.
  const [nodeTypeFilter, setNodeTypeFilterState] = useState<string[] | null>(null);
  const setSelectedRepo = (repoId: string) => {
    setSelectedRepoId(repoId);
    setNodeTypeFilterState(null);
  };

  // Cheap initial load: repository list — enough to render one overview
  // card per repository once each one's indexing summary (below) resolves.
  const repositoriesQuery = useQuery({
    queryKey: ["repositories"],
    queryFn: ({ signal }) => listTrackedRepositories(token as string, signal),
    enabled: token !== null,
  });
  const repositories = repositoriesQuery.data ?? [];

  // One lightweight indexing-summary query per repository (not its full
  // graph) — each repository's card can render as soon as its own summary
  // resolves, rather than waiting for the slowest one.
  const summaryQueries = useQueries({
    queries: repositories.map((repo) => ({
      queryKey: ["indexing-job", repo.id],
      queryFn: ({ signal }: { signal: AbortSignal }) =>
        getLatestIndexingJob(token as string, repo.id, signal).then((job) => job.result_summary),
      enabled: token !== null,
    })),
  });
  const summariesByRepoId = Object.fromEntries(
    repositories.map((repo, i) => [repo.id, summaryQueries[i]?.data ?? null]),
  );

  // Overview repository-to-repository edges: one request for every tracked
  // repository's cross-repository links at once (one Neo4j relationship
  // query server-side), fetched once and cached - never per hover, and no
  // longer one HTTP request per repository.
  const allLinksQuery = useQuery({
    queryKey: ["cross-repo-links-all"],
    queryFn: ({ signal }) => getAllCrossRepositoryLinks(token as string, signal),
    enabled: token !== null && repositories.length > 0,
  });
  // Structural repo-to-repo edges (CALLS_SERVICE/SHARES_TOPIC/
  // DEPENDS_ON_REPOSITORY) - a separate source from `allLinksQuery` above
  // (which only covers Kafka topic overlap), merged together below.
  const allEdgesQuery = useQuery({
    queryKey: ["cross-repo-edges-all"],
    queryFn: ({ signal }) => getAllCrossRepositoryEdges(token as string, signal),
    enabled: token !== null && repositories.length > 0,
  });

  // Lazy load: only fetch a repository's own graph once it's expanded -
  // never any other repository's full graph. Keyed on the active node-type
  // filter so a filtered view caches separately from the unfiltered one
  // (see `nodeTypeFilterKey`).
  const repoGraphQuery = useQuery({
    queryKey: ["repo-graph", selectedRepoId, nodeTypeFilterKey(nodeTypeFilter)],
    queryFn: ({ signal }) =>
      getRepositoryGraph(
        token as string,
        selectedRepoId,
        { nodeTypes: nodeTypeFilter ?? undefined },
        signal,
      ),
    enabled: token !== null && selectedRepoId !== "all",
    gcTime: GRAPH_QUERY_GC_TIME_MS,
  });
  // The *unfiltered* graph, fetched independently of whatever filter is
  // currently active - this is what the filter option list itself is
  // derived from below, so applying a filter never narrows its own set of
  // options. Shares a query key (and therefore a cache entry) with
  // `repoGraphQuery` above whenever no filter is active, so this adds no
  // extra request in the common case.
  const unfilteredGraphQuery = useQuery({
    queryKey: ["repo-graph", selectedRepoId, nodeTypeFilterKey(null)],
    queryFn: ({ signal }) => getRepositoryGraph(token as string, selectedRepoId, {}, signal),
    enabled: token !== null && selectedRepoId !== "all",
    gcTime: GRAPH_QUERY_GC_TIME_MS,
  });
  // One lightweight `/cross-repository-links` call per repository to
  // discover which other repositories it's connected to - cached per
  // repository regardless of the active node-type filter, since the filter
  // never changes which repositories are linked.
  const repoLinksQuery = useQuery({
    queryKey: ["repo-cross-links", selectedRepoId],
    queryFn: ({ signal }) => getCrossRepositoryLinks(token as string, selectedRepoId, signal),
    enabled: token !== null && selectedRepoId !== "all",
  });

  const isLoading = repositoriesQuery.isPending;
  const isLoadingSelectedGraph = repoGraphQuery.isPending || repoLinksQuery.isPending;
  const error =
    repositoriesQuery.error instanceof Error
      ? repositoriesQuery.error.message
      : repositoriesQuery.isError
        ? "Failed to load repositories."
        : null;

  const allLinks: CrossRepositoryLink[] = allLinksQuery.data ?? [];
  const structuralEdges = allEdgesQuery.data ?? [];

  // The full set of node-type filter options for this repository, derived
  // from its unfiltered graph load - never from `graph` below, since once a
  // type filter is applied `graph` only contains the selected types and its
  // own legend could no longer offer the *other* types back as options.
  // Known Phase-1 limitation: if the unfiltered load was itself truncated
  // (`total_node_count` cut off before every type appeared), a type
  // entirely past that cutoff won't be offered as a filter option until a
  // dedicated types-listing endpoint exists (see the Architecture Page
  // Scale Redesign doc, §7).
  const availableNodeTypes = useMemo(
    () => (unfilteredGraphQuery.data ? legendLabelsFor(unfilteredGraphQuery.data.nodes) : []),
    [unfilteredGraphQuery.data],
  );

  const graph = useMemo(
    () =>
      repoGraphQuery.data
        ? mergeCrossRepositoryLinks(repoGraphQuery.data, repoLinksQuery.data ?? [])
        : null,
    [repoGraphQuery.data, repoLinksQuery.data],
  );

  const repositoryNameById = Object.fromEntries(repositories.map((r) => [r.id, r.full_name]));
  const repositorySummaries: RepositorySummary[] = repositories.map((r) => ({
    id: r.id,
    name: r.full_name,
    ...summarizeRepositoryCounts(summariesByRepoId[r.id]),
  }));
  const repositoryDependencyEdges = mergeRepositoryDependencyEdges(
    allLinks ? buildRepositoryDependencyEdges(allLinks) : [],
    structuralEdges ? buildStructuralDependencyEdges(structuralEdges) : [],
  );
  const legendNodeLabels = graph ? legendLabelsFor(graph.nodes) : [];
  const legendEdges = graph ? edgeLegendFor(graph.edges) : [];

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold text-fg">Architecture</h1>
          <p className="mt-1 text-sm text-fg-muted">
            The dependency graph generated from indexed repositories, showing relationships between
            repositories, modules, services, APIs, data stores, messaging systems, and other
            software components.
          </p>
        </div>
        {repositories.length > 0 && (
          <label className="flex flex-col gap-1 text-xs text-fg-muted">
            Repository
            <select
              value={selectedRepoId}
              onChange={(e) => setSelectedRepo(e.target.value)}
              className="rounded-md border border-line bg-surface px-3 py-1.5 text-sm text-fg"
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
        <div className="rounded-lg border border-danger-line/30 bg-danger-bg px-4 py-3 text-sm text-danger-fg">
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
              onClick={() => setSelectedRepo("all")}
              className="rounded-md border border-line px-3 py-1.5 text-sm text-fg-secondary hover:border-line-strong"
            >
              ← Back to overview
            </button>
          )
        }
      >
        {isLoading ? (
          <div className="flex min-h-48 items-center justify-center text-sm text-fg-muted">
            Loading repositories…
          </div>
        ) : repositories.length === 0 ? (
          <EmptyState
            illustration={<SampleGraph />}
            title="Your architecture graph appears here"
            description="GraphForge indexes your repositories and maps every service, API, topic, and table — plus how they depend on each other. Connect GitHub and select a repository to build the first one."
            actions={[
              { label: "Connect GitHub", to: "/settings" },
              { label: "Manage repositories", to: "/repositories" },
            ]}
          />
        ) : selectedRepoId === "all" ? (
          <RepositoryOverviewGraph
            repositories={repositorySummaries}
            edges={repositoryDependencyEdges}
            onExpand={(repoId) => setSelectedRepo(repoId)}
          />
        ) : isLoadingSelectedGraph || !graph ? (
          <div className="flex min-h-48 items-center justify-center text-sm text-fg-muted">
            Loading this repository's graph…
          </div>
        ) : graph.nodes.length === 0 ? (
          <EmptyState
            illustration={<SampleGraph />}
            title="This repository hasn't been indexed"
            description="It's tracked, but no architecture graph exists for it yet. Indexing parses the source with tree-sitter and writes its components and relationships to the graph."
            actions={[
              { label: "Index this repository", to: `/repositories/${selectedRepoId}` },
              { label: "Back to overview", onClick: () => setSelectedRepo("all") },
            ]}
          />
        ) : (
          <div className="flex flex-col gap-3">
            {graph.truncated && (
              <div className="flex flex-wrap items-center gap-2 rounded-lg border border-warning-line/30 bg-warning-bg px-4 py-3 text-sm text-warning-fg">
                <span>
                  Showing {graph.nodes.length.toLocaleString()} of{" "}
                  {(graph.total_node_count ?? graph.nodes.length).toLocaleString()} nodes in this
                  repository — narrow with a type filter below to bring the rest into view.
                </span>
              </div>
            )}
            {availableNodeTypes.length > 1 && (
              <div className="flex flex-wrap items-center gap-2 text-xs">
                <span className="text-fg-muted">Filter by type:</span>
                {availableNodeTypes.map((label) => {
                  const active = nodeTypeFilter?.includes(label) ?? false;
                  return (
                    <button
                      key={label}
                      type="button"
                      aria-pressed={active}
                      onClick={() =>
                        setNodeTypeFilterState((current) => {
                          const next = new Set(current ?? []);
                          if (next.has(label)) next.delete(label);
                          else next.add(label);
                          return next.size === 0 ? null : [...next];
                        })
                      }
                      className={`rounded-full border px-2.5 py-1 transition-colors ${
                        active
                          ? "border-info-line bg-info-bg text-info-fg"
                          : "border-line text-fg-secondary hover:border-line-strong"
                      }`}
                    >
                      {label}
                    </button>
                  );
                })}
                {nodeTypeFilter && nodeTypeFilter.length > 0 && (
                  <button
                    type="button"
                    onClick={() => setNodeTypeFilterState(null)}
                    className="text-fg-muted underline hover:text-fg-secondary"
                  >
                    Clear
                  </button>
                )}
              </div>
            )}
            <DependencyGraph graph={graph} repositoryNameById={repositoryNameById} />
          </div>
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
              <p className="mb-2 text-xs font-medium uppercase tracking-wide text-fg-muted">
                Nodes
              </p>
              <ul className="flex flex-col gap-1.5 text-sm text-fg-secondary">
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
              <p className="mb-2 text-xs font-medium uppercase tracking-wide text-fg-muted">
                Relationships
              </p>
              <ul className="flex flex-col gap-1.5 text-sm text-fg-secondary">
                {legendEdges.map(({ type, description }) => (
                  <li key={type}>
                    <span className="font-mono text-xs text-fg-muted">{type}</span> — {description}
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
