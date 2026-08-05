import { useEffect, useRef, useState } from "react";
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
import type { TrackedRepository } from "../types/github";
import type { CrossRepositoryLink, Graph, GraphEdge } from "../types/graph";

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

// A single repository's graph can be large; only this many distinct
// (repository, type-filter) graphs stay resident in memory at once,
// evicted oldest-first — replaces what used to be an unbounded
// `graphsByRepoId` cache that only ever grew for the life of the page (see
// the Architecture Page Scale Redesign doc's audit, "every expanded
// repository's full graph stays resident in memory for the session").
const MAX_CACHED_GRAPHS = 5;

/** Cache key for one (repository, type-filter) combination — a filtered
 * view of a repository caches separately from its unfiltered graph rather
 * than overwriting it, so toggling a filter off doesn't force a refetch of
 * what was already loaded. */
function graphCacheKey(repositoryId: string, nodeTypes: string[] | null): string {
  if (!nodeTypes || nodeTypes.length === 0) return repositoryId;
  return `${repositoryId}::${[...nodeTypes].sort().join(",")}`;
}

export function ArchitecturePage() {
  const { token } = useAuth();
  const [searchParams] = useSearchParams();
  const [repositories, setRepositories] = useState<TrackedRepository[]>([]);
  // Full node/edge graphs are only ever fetched lazily, per repository, the
  // first time that repository is expanded - never eagerly for the whole
  // org, so opening this page stays cheap regardless of how many
  // repositories are tracked. Keyed by `graphCacheKey` (repository +
  // active type filter, if any), capped at `MAX_CACHED_GRAPHS` via
  // `graphCacheOrderRef` below - LRU, not unbounded.
  const [graphsByRepoId, setGraphsByRepoId] = useState<Record<string, Graph>>({});
  const graphCacheOrderRef = useRef<string[]>([]);
  const [summariesByRepoId, setSummariesByRepoId] = useState<
    Record<string, Record<string, number> | null>
  >({});
  const [selectedRepoId, setSelectedRepoId] = useState<string>(
    searchParams.get("repository") ?? "all",
  );
  // Reset whenever the selected repository changes (a filter chosen for
  // one repository's node types has no meaning for another's) - see the
  // effect below.
  const [nodeTypeFilter, setNodeTypeFilter] = useState<string[] | null>(null);
  // The full set of node-type filter options for each repository, captured
  // once from that repository's first (always unfiltered - see the reset
  // effect above) load. Deliberately not derived from the currently-loaded
  // `graph` on every render: once a type filter is applied, `graph` only
  // contains the selected types, so its own legend could no longer offer
  // the *other* types back as options. Known Phase-1 limitation: if the
  // very first load was itself truncated (`total_node_count` cut off before
  // every type appeared), a type entirely past that cutoff won't be
  // offered as a filter option until a dedicated types-listing endpoint
  // exists (see the Architecture Page Scale Redesign doc, §7).
  const [availableNodeTypesByRepo, setAvailableNodeTypesByRepo] = useState<
    Record<string, string[]>
  >({});
  const [isLoading, setIsLoading] = useState(true);
  const [isLoadingSelectedGraph, setIsLoadingSelectedGraph] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Cached once for the overview - never refetched on hover.
  const [allLinks, setAllLinks] = useState<CrossRepositoryLink[] | null>(null);
  // Structural repo-to-repo edges (CALLS_SERVICE/SHARES_TOPIC/
  // DEPENDS_ON_REPOSITORY) - a separate source from `allLinks` above (which
  // only covers Kafka topic overlap), merged together below.
  const [structuralEdges, setStructuralEdges] = useState<GraphEdge[] | null>(null);

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
      const [links, edges] = await Promise.all([
        getAllCrossRepositoryLinks(token!).catch(() => []),
        getAllCrossRepositoryEdges(token!).catch(() => []),
      ]);
      if (!cancelled) {
        setAllLinks(links);
        setStructuralEdges(edges);
      }
    }

    void loadLinks();
    return () => {
      cancelled = true;
    };
  }, [token, repositories, allLinks]);

  // A filter chosen for one repository's node types has no meaning once a
  // different repository is selected.
  useEffect(() => {
    setNodeTypeFilter(null);
  }, [selectedRepoId]);

  // Lazy load: only fetch a repository's own graph once it's expanded, plus
  // one lightweight `/cross-repository-links` call to discover which other
  // repositories it's connected to - never any other repository's full
  // graph. Exactly two requests per expand (three when a type filter is
  // active and that filtered view hasn't been cached yet).
  useEffect(() => {
    if (!token || selectedRepoId === "all") {
      return;
    }
    const cacheKey = graphCacheKey(selectedRepoId, nodeTypeFilter);
    if (graphsByRepoId[cacheKey]) {
      // Already cached - still bump it to most-recently-used so browsing
      // back to it doesn't leave it looking evictable while genuinely
      // unused entries stay resident.
      graphCacheOrderRef.current = [
        ...graphCacheOrderRef.current.filter((k) => k !== cacheKey),
        cacheKey,
      ];
      return;
    }
    let cancelled = false;

    async function loadSelected() {
      setIsLoadingSelectedGraph(true);
      const [ownGraph, links] = await Promise.all([
        getRepositoryGraph(token!, selectedRepoId, {
          nodeTypes: nodeTypeFilter ?? undefined,
        }),
        getCrossRepositoryLinks(token!, selectedRepoId).catch(() => []),
      ]);
      if (cancelled) return;
      const merged = mergeCrossRepositoryLinks(ownGraph, links);
      if (nodeTypeFilter === null) {
        setAvailableNodeTypesByRepo((prev) => ({
          ...prev,
          [selectedRepoId]: legendLabelsFor(merged.nodes),
        }));
      }
      setGraphsByRepoId((prev) => {
        const order = [...graphCacheOrderRef.current.filter((k) => k !== cacheKey), cacheKey];
        const next = { ...prev, [cacheKey]: merged };
        // LRU eviction: drop the oldest entries once the cap is exceeded -
        // replaces what used to be an unbounded cache (see MAX_CACHED_GRAPHS).
        while (order.length > MAX_CACHED_GRAPHS) {
          const evicted = order.shift();
          if (evicted) delete next[evicted];
        }
        graphCacheOrderRef.current = order;
        return next;
      });
      setIsLoadingSelectedGraph(false);
    }

    void loadSelected();
    return () => {
      cancelled = true;
    };
  }, [token, selectedRepoId, nodeTypeFilter, graphsByRepoId]);

  const graph: Graph | null =
    selectedRepoId === "all"
      ? null
      : (graphsByRepoId[graphCacheKey(selectedRepoId, nodeTypeFilter)] ?? null);
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
          <h2 className="text-xl font-semibold text-fg">Architecture</h2>
          <p className="mt-1 text-sm text-fg-muted">
            The dependency graph generated from indexed repositories, showing relationships
            between repositories, modules, services, APIs, data stores, messaging systems, and
            other software components.
          </p>
        </div>
        {repositories.length > 0 && (
          <label className="flex flex-col gap-1 text-xs text-fg-muted">
            Repository
            <select
              value={selectedRepoId}
              onChange={(e) => setSelectedRepoId(e.target.value)}
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
              onClick={() => setSelectedRepoId("all")}
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
          <div className="flex min-h-48 flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-line bg-canvas text-fg-muted">
            <p className="text-sm">No repositories tracked yet.</p>
          </div>
        ) : selectedRepoId === "all" ? (
          <RepositoryOverviewGraph
            repositories={repositorySummaries}
            edges={repositoryDependencyEdges}
            onExpand={(repoId) => setSelectedRepoId(repoId)}
          />
        ) : isLoadingSelectedGraph || !graph ? (
          <div className="flex min-h-48 items-center justify-center text-sm text-fg-muted">
            Loading this repository's graph…
          </div>
        ) : graph.nodes.length === 0 ? (
          <div className="flex min-h-48 flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-line bg-canvas text-fg-muted">
            <p className="text-sm">No graph data yet - index this repository first.</p>
          </div>
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
            {(availableNodeTypesByRepo[selectedRepoId]?.length ?? 0) > 1 && (
              <div className="flex flex-wrap items-center gap-2 text-xs">
                <span className="text-fg-muted">Filter by type:</span>
                {availableNodeTypesByRepo[selectedRepoId].map((label) => {
                  const active = nodeTypeFilter?.includes(label) ?? false;
                  return (
                    <button
                      key={label}
                      type="button"
                      aria-pressed={active}
                      onClick={() =>
                        setNodeTypeFilter((current) => {
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
                    onClick={() => setNodeTypeFilter(null)}
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
