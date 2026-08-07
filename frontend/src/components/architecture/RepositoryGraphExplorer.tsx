import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Search, X } from "lucide-react";
import { Card } from "../Card";
import { EmptyState, SampleGraph } from "../EmptyState";
import { DependencyGraph } from "../graph/DependencyGraph";
import { DomainEditor } from "./DomainEditor";
import { NodeDetailPanel } from "./NodeDetailPanel";
import { useAuth } from "../../app/auth-context";
import {
  getRepositoryGraph,
  getRepositoryGraphNodeNeighbors,
  getRepositoryGraphTypes,
} from "../../lib/api/repositories";
import type { Graph, GraphNode } from "../../types/graph";

/** Full-repository pages load this many nodes at a time — small enough
 * that even a slow connection sees the first page quickly, large enough
 * that most real repositories (a few hundred to a couple thousand nodes)
 * need at most one "Load more" click. */
const PAGE_SIZE = 500;
const DEFAULT_NEIGHBOR_HOPS = 1;

/** Stable key for a node-type filter combination, mirroring the previous
 * page's own `nodeTypeFilterKey` — a filtered view caches separately from
 * the unfiltered one instead of overwriting it. */
function nodeTypeFilterKey(nodeTypes: string[] | null): string {
  if (!nodeTypes || nodeTypes.length === 0) return "__all__";
  return [...nodeTypes].sort().join(",");
}

function mergeGraphs(pages: Graph[]): Graph {
  const nodesById = new Map<string, GraphNode>();
  const edgeKeys = new Set<string>();
  const edges: Graph["edges"] = [];
  for (const page of pages) {
    for (const node of page.nodes) nodesById.set(node.id, node);
    for (const edge of page.edges) {
      const key = `${edge.source_id}->${edge.target_id}-${edge.type}`;
      if (edgeKeys.has(key)) continue;
      edgeKeys.add(key);
      edges.push(edge);
    }
  }
  const last = pages[pages.length - 1];
  return {
    nodes: [...nodesById.values()],
    edges,
    truncated: last?.truncated ?? false,
    total_node_count: last?.total_node_count ?? null,
    next_cursor: last?.next_cursor ?? null,
  };
}

type Mode =
  | { kind: "full" }
  | { kind: "neighborhood"; nodeId: string };

/** ADR "Architecture Page V2" — the graph canvas shared by both the
 * full-repository (lazy-paginated, type-filterable, searchable) and
 * neighborhood (hop-bounded, reached via "Explore neighbors") views.
 * They differ only in *how* `Graph` data is fetched, not in how it's
 * rendered or how node selection/detail/expand-neighbors behaves — kept
 * as one component with a `mode` switch rather than two near-duplicates.
 */
export function RepositoryGraphExplorer({
  repositoryId,
  repositoryName,
  mode,
  onExploreNeighbors,
  domain = null,
  onDomainChange,
}: {
  repositoryId: string;
  repositoryName: string;
  mode: Mode;
  onExploreNeighbors: (node: GraphNode) => void;
  /** Undefined in neighborhood mode (no header action there — it's a
   * transient drill-down, not the repository's own detail view). */
  domain?: string | null;
  onDomainChange?: (domain: string | null) => Promise<void>;
}) {
  const { token } = useAuth();
  const [nodeTypeFilter, setNodeTypeFilter] = useState<string[] | null>(null);
  const [loadedPages, setLoadedPages] = useState<Graph[]>([]);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [query, setQuery] = useState("");
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null);
  const [isExploringNeighbors, setIsExploringNeighbors] = useState(false);
  const [isSavingDomain, setIsSavingDomain] = useState(false);

  async function handleDomainSave(next: string | null) {
    if (!onDomainChange) return;
    setIsSavingDomain(true);
    try {
      await onDomainChange(next);
    } finally {
      setIsSavingDomain(false);
    }
  }

  // Real, untruncated per-label counts — only meaningful (and only
  // fetched) in full-repository mode; a neighborhood is already small
  // enough that a type-filter UI over it would be solving a problem that
  // doesn't exist at that scale.
  const typesQuery = useQuery({
    queryKey: ["repo-graph-types", repositoryId],
    queryFn: ({ signal }) => getRepositoryGraphTypes(token as string, repositoryId, signal),
    enabled: token !== null && mode.kind === "full",
  });

  const filterKey = nodeTypeFilterKey(nodeTypeFilter);
  const firstPageQuery = useQuery({
    queryKey: ["repo-graph-page", repositoryId, mode.kind === "neighborhood" ? mode.nodeId : filterKey],
    queryFn: async ({ signal }) => {
      if (mode.kind === "neighborhood") {
        return getRepositoryGraphNodeNeighbors(
          token as string,
          repositoryId,
          mode.nodeId,
          { hops: DEFAULT_NEIGHBOR_HOPS },
          signal,
        );
      }
      return getRepositoryGraph(
        token as string,
        repositoryId,
        { limit: PAGE_SIZE, nodeTypes: nodeTypeFilter ?? undefined },
        signal,
      );
    },
    enabled: token !== null,
  });

  // `loadedPages` accumulates additional pages fetched via "Load more" —
  // reset whenever the underlying query identity changes (repo, mode,
  // filter) since a fresh first page invalidates whatever was appended
  // to a previous one.
  const pages = useMemo(
    () => (firstPageQuery.data ? [firstPageQuery.data, ...loadedPages] : []),
    [firstPageQuery.data, loadedPages],
  );
  const graph = useMemo(() => (pages.length > 0 ? mergeGraphs(pages) : null), [pages]);

  async function handleLoadMore() {
    if (!graph?.next_cursor || !token || mode.kind !== "full") return;
    setIsLoadingMore(true);
    try {
      const next = await getRepositoryGraph(token, repositoryId, {
        limit: PAGE_SIZE,
        nodeTypes: nodeTypeFilter ?? undefined,
        after: graph.next_cursor,
      });
      setLoadedPages((current) => [...current, next]);
    } finally {
      setIsLoadingMore(false);
    }
  }

  function handleFilterChange(next: string[] | null) {
    setNodeTypeFilter(next);
    setLoadedPages([]);
    setSelectedNode(null);
  }

  async function handleExploreNeighbors() {
    if (!selectedNode) return;
    setIsExploringNeighbors(true);
    try {
      onExploreNeighbors(selectedNode);
    } finally {
      setIsExploringNeighbors(false);
    }
  }

  // Search highlights/filters within whatever is *already loaded* — a
  // client-side match over the current page(s), not a server-side search
  // across the repository's full graph (no such endpoint exists yet; see
  // ADR 0023's own "explicitly deferred" list). At the scale this page is
  // built for (lazy-loaded, hundreds-of-thousands-of-nodes repositories),
  // "search everything, always" would mean fetching everything, always —
  // exactly the N+1-successor problem this redesign exists to avoid.
  const displayedGraph = useMemo(() => {
    if (!graph || !query.trim()) return graph;
    const q = query.trim().toLowerCase();
    const matchingIds = new Set(
      graph.nodes
        .filter((n) => String(n.properties.name ?? n.id).toLowerCase().includes(q))
        .map((n) => n.id),
    );
    return {
      ...graph,
      nodes: graph.nodes.filter((n) => matchingIds.has(n.id)),
      edges: graph.edges.filter((e) => matchingIds.has(e.source_id) && matchingIds.has(e.target_id)),
    };
  }, [graph, query]);

  const nodeTypeCounts = typesQuery.data?.counts ?? {};
  const availableNodeTypes = Object.keys(nodeTypeCounts).sort(
    (a, b) => nodeTypeCounts[b] - nodeTypeCounts[a] || a.localeCompare(b),
  );

  const isLoading = firstPageQuery.isPending;

  return (
    <Card
      title={repositoryName}
      description={
        mode.kind === "neighborhood"
          ? `Nodes within ${DEFAULT_NEIGHBOR_HOPS} hop of the selected node.`
          : "This repository's architecture graph, loaded progressively."
      }
      action={
        mode.kind === "full" && onDomainChange ? (
          <DomainEditor domain={domain} onSave={handleDomainSave} isSaving={isSavingDomain} />
        ) : undefined
      }
    >
      {isLoading ? (
        // Matches MetricsPage's/the landing page's own skeleton convention
        // rather than plain "Loading…" text.
        <div className="flex flex-col gap-3" aria-busy="true" aria-label="Loading graph">
          <div className="h-8 w-64 animate-pulse rounded-md bg-surface-raised" />
          <div className="h-[clamp(20rem,70vh,45rem)] animate-pulse rounded-xl bg-surface" />
        </div>
      ) : !graph || graph.nodes.length === 0 ? (
        <EmptyState
          illustration={<SampleGraph />}
          title={mode.kind === "neighborhood" ? "No neighbors found" : "This repository hasn't been indexed"}
          description={
            mode.kind === "neighborhood"
              ? "This node has no connected components within range."
              : "It's tracked, but no architecture graph exists for it yet."
          }
        />
      ) : (
        <div className="flex flex-col gap-3">
          <div className="flex flex-wrap items-center gap-3">
            {mode.kind === "full" && availableNodeTypes.length > 1 && (
              <div className="flex flex-wrap items-center gap-2 text-xs">
                <span className="text-fg-muted">Filter by type:</span>
                {availableNodeTypes.map((label) => {
                  const active = nodeTypeFilter?.includes(label) ?? false;
                  return (
                    <button
                      key={label}
                      type="button"
                      aria-pressed={active}
                      onClick={() => {
                        const next = new Set(nodeTypeFilter ?? []);
                        if (next.has(label)) next.delete(label);
                        else next.add(label);
                        handleFilterChange(next.size === 0 ? null : [...next]);
                      }}
                      className={`rounded-full border px-2.5 py-1 transition-colors ${
                        active
                          ? "border-info-line bg-info-bg text-info-fg"
                          : "border-line text-fg-secondary hover:border-line-strong"
                      }`}
                    >
                      {label} ({nodeTypeCounts[label]?.toLocaleString()})
                    </button>
                  );
                })}
                {nodeTypeFilter && nodeTypeFilter.length > 0 && (
                  <button
                    type="button"
                    onClick={() => handleFilterChange(null)}
                    className="text-fg-muted underline hover:text-fg-secondary"
                  >
                    Clear
                  </button>
                )}
              </div>
            )}
            <div className="relative ml-auto w-full max-w-xs">
              <Search
                className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-fg-muted"
                aria-hidden="true"
              />
              <input
                type="text"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Escape" && query) {
                    e.stopPropagation();
                    setQuery("");
                  }
                }}
                placeholder="Find a loaded node…"
                aria-label="Search loaded nodes"
                className="w-full rounded-md border border-line-strong bg-canvas py-1.5 pl-8 pr-7 text-xs text-fg-secondary placeholder-fg-subtle focus:outline-none focus:ring-1 focus:ring-info-fg"
              />
              {query && (
                <button
                  type="button"
                  onClick={() => setQuery("")}
                  aria-label="Clear search"
                  className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded p-0.5 text-fg-muted hover:text-fg-secondary"
                >
                  <X className="h-3.5 w-3.5" aria-hidden="true" />
                </button>
              )}
            </div>
          </div>

          {graph.truncated && (
            <div className="flex flex-wrap items-center gap-3 rounded-lg border border-warning-line/30 bg-warning-bg px-4 py-3 text-sm text-warning-fg">
              <span>
                Loaded {graph.nodes.length.toLocaleString()} of{" "}
                {(graph.total_node_count ?? graph.nodes.length).toLocaleString()} nodes.
              </span>
              <button
                type="button"
                onClick={() => void handleLoadMore()}
                disabled={isLoadingMore}
                className="rounded-md border border-warning-line/50 px-2.5 py-1 text-xs font-medium hover:bg-warning-bg/70 disabled:cursor-not-allowed"
              >
                {isLoadingMore ? "Loading…" : "Load more"}
              </button>
            </div>
          )}

          <div className="flex overflow-hidden rounded-xl border border-line">
            <div className="min-w-0 flex-1">
              <DependencyGraph
                graph={displayedGraph ?? graph}
                onNodeSelect={setSelectedNode}
                selectedNodeId={selectedNode?.id ?? null}
              />
            </div>
            {selectedNode && (
              <NodeDetailPanel
                node={selectedNode}
                onClose={() => setSelectedNode(null)}
                onExploreNeighbors={() => void handleExploreNeighbors()}
                isExploringNeighbors={isExploringNeighbors}
              />
            )}
          </div>
        </div>
      )}
    </Card>
  );
}
