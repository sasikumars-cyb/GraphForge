import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, ArrowRight } from "lucide-react";
import { Card } from "../Card";
import { EmptyState, SampleGraph } from "../EmptyState";
import { DependencyGraph } from "../graph/DependencyGraph";
import { NodeDetailPanel } from "../architecture/NodeDetailPanel";
import { useAuth } from "../../app/auth-context";
import { getRepositoryGraphNodeNeighbors, type NeighborDirection } from "../../lib/api/repositories";
import type { Graph, GraphNode } from "../../types/graph";

const DEFAULT_HOPS = 1;

/** Every graph node's id is namespaced `{repository_id}:...`; every
 * repository also has exactly one canonical `{repository_id}:repository`
 * hub node (the same default seed `GET /repositories/{id}/impact` already
 * uses) — the natural root for "what does this repository depend on,"
 * with no separate node picker needed for a first pass. */
function repositoryRootId(repositoryId: string): string {
  return `${repositoryId}:repository`;
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
  return { nodes: [...nodesById.values()], edges };
}

/**
 * The Dependency lens (ARCHITECTURE_EXPERIENCE_REDESIGN.md) — an
 * expandable dependency tree rooted at the repository, not a ranked list.
 * Reuses exactly the mechanics that section explicitly calls out as
 * already built: `DependencyGraph`'s horizontal (dagre `rankdir: "LR"`)
 * layout is already the tree shape this lens wants, and expand-on-click
 * reuses the same lazy neighbor-fetch the Architecture lens already has —
 * only the `direction` toggle (now that `get_neighborhood` actually
 * honors it) and the cumulative-merge-instead-of-replace behavior (a
 * growing tree, not a page navigation) are new here.
 *
 * Scope cuts, mirroring how Impact Check itself was scoped tightly rather
 * than building the full lens description in one pass: no subtree
 * collapse, no depth-cap slider, no trace-path-between-two-nodes, no
 * search-to-expand. One root (the repository hub), one hop at a time,
 * grown by clicking — the smallest slice that's still a real, visual,
 * five-second read of "shallow and narrow" vs. "wide and deep."
 */
export function DependencyTreeExplorer({
  repositoryId,
  repositoryName,
}: {
  repositoryId: string;
  repositoryName: string;
}) {
  const { token } = useAuth();
  const [direction, setDirection] = useState<NeighborDirection>("outgoing");
  const [expandedPages, setExpandedPages] = useState<Graph[]>([]);
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null);
  const [isExpanding, setIsExpanding] = useState(false);

  const rootId = repositoryRootId(repositoryId);

  const rootQuery = useQuery({
    queryKey: ["dependency-tree-root", repositoryId, direction],
    queryFn: ({ signal }) =>
      getRepositoryGraphNodeNeighbors(
        token as string,
        repositoryId,
        rootId,
        { hops: DEFAULT_HOPS, direction },
        signal,
      ),
    enabled: token !== null,
  });

  const pages = useMemo(
    () => (rootQuery.data ? [rootQuery.data, ...expandedPages] : []),
    [rootQuery.data, expandedPages],
  );
  const graph = useMemo(() => (pages.length > 0 ? mergeGraphs(pages) : null), [pages]);

  function changeDirection(next: NeighborDirection) {
    if (next === direction) return;
    setDirection(next);
    // A node reachable "downstream" isn't generally reachable "upstream"
    // from the same root — a tree grown in one direction doesn't carry
    // over when the direction flips, so start fresh rather than show a
    // half-outgoing, half-incoming graph.
    setExpandedPages([]);
    setSelectedNode(null);
  }

  async function handleExpand() {
    if (!selectedNode || !token) return;
    setIsExpanding(true);
    try {
      const next = await getRepositoryGraphNodeNeighbors(token, repositoryId, selectedNode.id, {
        hops: DEFAULT_HOPS,
        direction,
      });
      setExpandedPages((current) => [...current, next]);
    } finally {
      setIsExpanding(false);
    }
  }

  const isLoading = rootQuery.isPending;
  const directionLabel = direction === "outgoing" ? "depends on" : "is depended on by";

  return (
    <Card
      title={repositoryName}
      description={`What ${repositoryName} ${directionLabel}, expanded one hop at a time.`}
      action={
        <div
          role="group"
          aria-label="Dependency direction"
          className="flex items-center gap-0.5 rounded-full border border-line p-0.5 text-xs"
        >
          <button
            type="button"
            aria-pressed={direction === "outgoing"}
            onClick={() => changeDirection("outgoing")}
            className={`flex items-center gap-1 rounded-full px-2.5 py-1 transition-colors ${
              direction === "outgoing"
                ? "bg-info-bg text-info-fg"
                : "text-fg-secondary hover:bg-surface-raised"
            }`}
          >
            <ArrowRight className="h-3.5 w-3.5" aria-hidden="true" />
            Depends on
          </button>
          <button
            type="button"
            aria-pressed={direction === "incoming"}
            onClick={() => changeDirection("incoming")}
            className={`flex items-center gap-1 rounded-full px-2.5 py-1 transition-colors ${
              direction === "incoming"
                ? "bg-info-bg text-info-fg"
                : "text-fg-secondary hover:bg-surface-raised"
            }`}
          >
            <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" />
            Depended on by
          </button>
        </div>
      }
    >
      {isLoading ? (
        <div className="flex flex-col gap-3" aria-busy="true" aria-label="Loading dependency tree">
          <div className="h-8 w-64 animate-pulse rounded-md bg-surface-raised" />
          <div className="h-[clamp(20rem,70vh,45rem)] animate-pulse rounded-xl bg-surface" />
        </div>
      ) : !graph || graph.nodes.length <= 1 ? (
        <EmptyState
          illustration={<SampleGraph />}
          title={
            direction === "outgoing"
              ? "No outgoing dependencies found"
              : "Nothing depends on this repository"
          }
          description={
            direction === "outgoing"
              ? "This repository doesn't call out to anything else in the graph."
              : "No other tracked component points at this repository."
          }
        />
      ) : (
        <div className="flex overflow-hidden rounded-xl border border-line">
          <div className="min-w-0 flex-1">
            <DependencyGraph
              graph={graph}
              onNodeSelect={setSelectedNode}
              selectedNodeId={selectedNode?.id ?? null}
            />
          </div>
          {selectedNode && (
            <NodeDetailPanel
              node={selectedNode}
              onClose={() => setSelectedNode(null)}
              onExploreNeighbors={() => void handleExpand()}
              isExploringNeighbors={isExpanding}
              exploreLabel="Expand dependencies"
              exploringLabel="Expanding…"
            />
          )}
        </div>
      )}
    </Card>
  );
}
