import { useMemo, useState } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  Position,
  MarkerType,
  type Node,
  type Edge,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import dagre from "@dagrejs/dagre";
import type { Graph } from "../../types/graph";
import { primaryLabel, resolveLabelColors } from "./graphLabels";
import { useTheme } from "../../theme/theme-context";

const NODE_WIDTH = 190;
const NODE_HEIGHT = 56;

// Node-focused highlighting palette (DependencyGraph only). These are the
// *relationship* colours — deliberately drawn from the semantic roles rather
// than the categorical node slots, so a highlight can never be confused with
// a node type. The selection colour used to be a hardcoded `#facc15` gold,
// which sat at ~1.9:1 on the light canvas and clashed with the amber warning
// tone; it is now the theme's accent.
const SELECTED_COLOR = "var(--gf-graph-selected)";
const INCOMING_COLOR = "var(--gf-graph-incoming)";
const OUTGOING_COLOR = "var(--gf-graph-outgoing)";
// Dimming for nodes outside the selection. 0.15 pushed unrelated nodes below
// the point where their labels could be read at all; the highlight ring and
// z-order already carry the emphasis, so the dim only needs to recede.
const FADED_OPACITY = 0.3;

/** A translucent wash of `color`, for glows/rings. Hex-with-alpha suffixes
 *  (`${c}55`) cannot work here because these colours are `var()` references. */
function wash(color: string, percent: number): string {
  return `color-mix(in srgb, ${color} ${percent}%, transparent)`;
}

const GROUP_PADDING = 28;

/** Every graph node's id is namespaced as `{repository_id}:...`. */
function repositoryIdOf(nodeId: string): string {
  return nodeId.split(":")[0];
}

function layoutGraph(
  graph: Graph,
  repositoryNameById?: Record<string, string>,
): { nodes: Node[]; edges: Edge[] } {
  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: "LR", nodesep: 40, ranksep: 110 });
  g.setDefaultEdgeLabel(() => ({}));

  for (const node of graph.nodes) {
    g.setNode(node.id, { width: NODE_WIDTH, height: NODE_HEIGHT });
  }
  for (const edge of graph.edges) {
    g.setEdge(edge.source_id, edge.target_id);
  }
  dagre.layout(g);

  const baseNodes = graph.nodes.map((node) => {
    const position = g.node(node.id);
    const label = primaryLabel(node.labels);
    const colors = resolveLabelColors(label);
    // A Repository node's own `properties.name` is rarely populated (the
    // indexer writes repository identity onto the row, not the graph node),
    // so this fell through to the raw `node.id` — `{uuid}:repository` — the
    // one label a reviewer most needs to read at a glance. `repositoryNameById`
    // (already threaded through for multi-repo cluster labels below) has the
    // real "owner/name" and takes priority for exactly this node type.
    const repoName =
      label === "Repository" ? repositoryNameById?.[repositoryIdOf(node.id)] : undefined;
    const name = String(repoName ?? node.properties.name ?? node.id);
    return {
      graphNode: node,
      absoluteX: position.x - NODE_WIDTH / 2,
      absoluteY: position.y - NODE_HEIGHT / 2,
      label,
      colors,
      name,
    };
  });

  const edges: Edge[] = graph.edges.map((edge, index) => ({
    id: `${edge.source_id}->${edge.target_id}-${edge.type}-${index}`,
    source: edge.source_id,
    target: edge.target_id,
    label: edge.type,
    animated: edge.type === "PRODUCES_TO" || edge.type === "CONSUMES_FROM",
    markerEnd: { type: MarkerType.ArrowClosed, color: "var(--gf-graph-edge)" },
    style: { stroke: "var(--gf-graph-edge)", strokeWidth: 1.5 },
    labelStyle: { fill: "var(--gf-graph-edge-label)", fontSize: 10 },
  }));

  // Group nodes by owning repository (hierarchical clustering) so a merged,
  // multi-repository graph stays readable instead of one undifferentiated
  // mass of nodes - only worth doing when more than one repo is present.
  const repoIds = new Set(
    baseNodes
      .map((n) => repositoryIdOf(n.graphNode.id))
      .filter((id) => repositoryNameById?.[id] !== undefined),
  );

  if (!repositoryNameById || repoIds.size < 2) {
    const nodes: Node[] = baseNodes.map(({ graphNode, absoluteX, absoluteY, label, colors, name }) => ({
      id: graphNode.id,
      position: { x: absoluteX, y: absoluteY },
      // Top-level width/height (not just style.width below) so the MiniMap's
      // nodeHasDimensions() check passes immediately from these known-upfront
      // dagre dimensions, instead of waiting on React Flow's post-mount
      // ResizeObserver measurement — without this the minimap renders zero
      // node rects (just the empty viewport mask) until/unless `measured`
      // happens to populate, which in practice never visibly did.
      width: NODE_WIDTH,
      height: NODE_HEIGHT,
      data: {
        label: (
          <div>
            <div style={{ fontSize: 10, opacity: 0.75 }}>{label}</div>
            <div style={{ fontWeight: 600 }}>{name}</div>
          </div>
        ),
      },
      style: {
        background: colors.background,
        border: `var(--gf-node-border-width, 1px) solid ${colors.border}`,
        borderRadius: 8,
        width: NODE_WIDTH,
        // Ink from the same slot as the fill — a shared neutral cannot be
        // readable on eight different fills across five themes.
        color: colors.text,
        fontSize: 12,
        padding: 8,
      },
      sourcePosition: Position.Right,
      targetPosition: Position.Left,
    }));
    return { nodes, edges };
  }

  const bounds = new Map<string, { minX: number; minY: number; maxX: number; maxY: number }>();
  for (const n of baseNodes) {
    const repoId = repositoryIdOf(n.graphNode.id);
    if (!repositoryNameById[repoId]) continue;
    const box = bounds.get(repoId) ?? {
      minX: Infinity,
      minY: Infinity,
      maxX: -Infinity,
      maxY: -Infinity,
    };
    box.minX = Math.min(box.minX, n.absoluteX);
    box.minY = Math.min(box.minY, n.absoluteY);
    box.maxX = Math.max(box.maxX, n.absoluteX + NODE_WIDTH);
    box.maxY = Math.max(box.maxY, n.absoluteY + NODE_HEIGHT);
    bounds.set(repoId, box);
  }

  const groupNodes: Node[] = [];
  for (const [repoId, box] of bounds) {
    const groupId = `group:${repoId}`;
    const top = box.minY - GROUP_PADDING - 20;
    const left = box.minX - GROUP_PADDING;
    groupNodes.push({
      id: groupId,
      type: "group",
      position: { x: left, y: top },
      // See the `nodes` map above — top-level width/height so the MiniMap
      // can size this cluster box without waiting on post-mount measurement.
      width: box.maxX - box.minX + GROUP_PADDING * 2,
      height: box.maxY - box.minY + GROUP_PADDING * 2 + 20,
      style: {
        width: box.maxX - box.minX + GROUP_PADDING * 2,
        height: box.maxY - box.minY + GROUP_PADDING * 2 + 20,
        background: "var(--gf-graph-cluster)",
        border: "1px dashed var(--gf-graph-cluster-line)",
        borderRadius: 12,
      },
      data: {},
      selectable: false,
      draggable: false,
    });
    groupNodes.push({
      id: `${groupId}-label`,
      type: "default",
      parentId: groupId,
      extent: "parent",
      position: { x: 8, y: 4 },
      data: { label: repositoryNameById[repoId] },
      style: {
        background: "transparent",
        border: "none",
        color: "var(--gf-fg-muted)",
        fontSize: 11,
        fontWeight: 700,
        padding: 0,
        width: "auto",
      },
      selectable: false,
      draggable: false,
      connectable: false,
    });
  }

  const childNodes: Node[] = baseNodes.map(({ graphNode, absoluteX, absoluteY, label, colors, name }) => {
    const repoId = repositoryIdOf(graphNode.id);
    const box = bounds.get(repoId);
    const groupId = box ? `group:${repoId}` : undefined;
    const top = box ? box.minY - GROUP_PADDING - 20 : 0;
    const left = box ? box.minX - GROUP_PADDING : 0;
    return {
      id: graphNode.id,
      position: groupId
        ? { x: absoluteX - left, y: absoluteY - top }
        : { x: absoluteX, y: absoluteY },
      parentId: groupId,
      extent: groupId ? "parent" : undefined,
      width: NODE_WIDTH,
      height: NODE_HEIGHT,
      data: {
        label: (
          <div>
            <div style={{ fontSize: 10, opacity: 0.75 }}>{label}</div>
            <div style={{ fontWeight: 600 }}>{name}</div>
          </div>
        ),
      },
      style: {
        background: colors.background,
        border: `var(--gf-node-border-width, 1px) solid ${colors.border}`,
        borderRadius: 8,
        width: NODE_WIDTH,
        // Ink from the same slot as the fill — a shared neutral cannot be
        // readable on eight different fills across five themes.
        color: colors.text,
        fontSize: 12,
        padding: 8,
      },
      sourcePosition: Position.Right,
      targetPosition: Position.Left,
    };
  });

  return { nodes: [...groupNodes, ...childNodes], edges };
}

const OVERVIEW_NODE_WIDTH = 220;
const OVERVIEW_NODE_HEIGHT = 120;
const OVERVIEW_COLUMNS = 4;

export interface RepositorySummary {
  id: string;
  name: string;
  components: number | undefined;
  externalDependencies: number | undefined;
  messagingTouchpoints: number | undefined;
}

export interface RepositoryDependencyEdge {
  source: string;
  target: string;
  topics: string[];
}

function overviewNode(repo: RepositorySummary, x: number, y: number): Node {
  return {
    id: repo.id,
    position: { x, y },
    data: {
      label: (
        <div>
          <div style={{ fontWeight: 700, marginBottom: 6 }}>{repo.name}</div>
          <div style={{ fontSize: 11, opacity: 0.85 }}>Components: {repo.components ?? "—"}</div>
          <div style={{ fontSize: 11, opacity: 0.85 }}>
            External dependencies: {repo.externalDependencies ?? "—"}
          </div>
          <div style={{ fontSize: 11, opacity: 0.85 }}>
            Messaging touchpoints: {repo.messagingTouchpoints ?? "—"}
          </div>
          <div style={{ fontSize: 10, opacity: 0.6, marginTop: 4 }}>
            Inbound dependencies: expand to see
          </div>
        </div>
      ),
    },
    style: {
      background: "var(--gf-surface-raised)",
      border: "1px solid var(--gf-line-strong)",
      borderRadius: 10,
      width: OVERVIEW_NODE_WIDTH,
      color: "var(--gf-fg-secondary)",
      fontSize: 12,
      padding: 10,
      cursor: "pointer",
    },
  };
}

/**
 * The initial "all repositories" view: one lightweight node per repository
 * (built from its already-fetched indexing summary counts, not its full
 * graph), so opening the page never fetches or renders internal nodes for
 * repositories the user hasn't asked to look at yet. Clicking a card is how
 * the user "expands" that repository into its detailed graph.
 *
 * Directed edges between cards are drawn from `edges` - both Kafka topic
 * overlap (from `/cross-repository-links`) and structural relationships
 * like Feign service calls or shared internal dependencies (from
 * `/cross-repository-edges`), merged once by the caller - no extra calls
 * here, and none at all on hover.
 */
export function RepositoryOverviewGraph({
  repositories,
  edges: dependencyEdges,
  onExpand,
}: {
  repositories: RepositorySummary[];
  edges: RepositoryDependencyEdge[];
  onExpand: (repositoryId: string) => void;
}) {
  const { theme } = useTheme();
  const { nodes, edges } = useMemo(() => {
    if (dependencyEdges.length === 0) {
      // No known relationships yet - a plain grid is clearer than an
      // empty-edge dagre layout.
      const gridNodes = repositories.map((repo, index) =>
        overviewNode(
          repo,
          (index % OVERVIEW_COLUMNS) * (OVERVIEW_NODE_WIDTH + 32),
          Math.floor(index / OVERVIEW_COLUMNS) * (OVERVIEW_NODE_HEIGHT + 32),
        ),
      );
      return { nodes: gridNodes, edges: [] };
    }

    const g = new dagre.graphlib.Graph();
    g.setGraph({ rankdir: "LR", nodesep: 40, ranksep: 140 });
    g.setDefaultEdgeLabel(() => ({}));
    for (const repo of repositories) {
      g.setNode(repo.id, { width: OVERVIEW_NODE_WIDTH, height: OVERVIEW_NODE_HEIGHT });
    }
    for (const edge of dependencyEdges) {
      g.setEdge(edge.source, edge.target);
    }
    dagre.layout(g);

    const laidOutNodes = repositories.map((repo) => {
      const position = g.node(repo.id);
      return overviewNode(
        repo,
        position.x - OVERVIEW_NODE_WIDTH / 2,
        position.y - OVERVIEW_NODE_HEIGHT / 2,
      );
    });

    const flowEdges: Edge[] = dependencyEdges.map((edge) => ({
      id: `${edge.source}->${edge.target}`,
      source: edge.source,
      target: edge.target,
      label: (
        <span title={`Relationships:\n${edge.topics.join("\n")}`}>{edge.topics.length}</span>
      ),
      markerEnd: { type: MarkerType.ArrowClosed, color: "var(--gf-graph-edge)" },
      style: { stroke: "var(--gf-graph-edge)", strokeWidth: 1.5 },
      labelBgStyle: { fill: "var(--gf-surface-raised)" },
      labelStyle: { fill: "var(--gf-fg-secondary)", fontSize: 11 },
    }));

    return { nodes: laidOutNodes, edges: flowEdges };
  }, [repositories, dependencyEdges]);

  return (
    // Viewport-relative rather than a fixed 480px: a graph is the primary
    // content of the surface it sits on, and a hardcoded height gave a
    // 6-node graph and a 2,000-node graph exactly the same window — while
    // wasting most of a tall desktop display. Clamped so it stays usable on
    // a short laptop viewport and never grows past a comfortable reading
    // size on a very tall one.
    <div className="h-[clamp(20rem,70vh,45rem)] overflow-hidden rounded-xl border border-line bg-graph-canvas">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        fitView
        proOptions={{ hideAttribution: true }}
        colorMode={theme.mode}
        onlyRenderVisibleElements
        onNodeClick={(_, node) => onExpand(node.id)}
      >
        <Background />
        <Controls />
      </ReactFlow>
    </div>
  );
}

interface DependencyGraphProps {
  graph: Graph;
  repositoryNameById?: Record<string, string>;
}

export function DependencyGraph({ graph, repositoryNameById }: DependencyGraphProps) {
  const { theme } = useTheme();
  const { nodes: baseNodes, edges: baseEdges } = useMemo(
    () => layoutGraph(graph, repositoryNameById),
    [graph, repositoryNameById],
  );
  // Group/label pseudo-nodes (clustering boxes) aren't real graph nodes and
  // are excluded from click-to-highlight - only actual components/topics/
  // endpoints participate.
  const realNodeIds = useMemo(() => new Set(graph.nodes.map((n) => n.id)), [graph]);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);

  // Recomputed only when the selection (or the base layout) changes - not on
  // every render - so clicking around a large graph stays cheap.
  const { nodes, edges } = useMemo(() => {
    if (!selectedNodeId) {
      return { nodes: baseNodes, edges: baseEdges };
    }

    const incoming = new Set<string>();
    const outgoing = new Set<string>();
    const connectedEdgeIds = new Set<string>();
    for (const edge of baseEdges) {
      if (edge.target === selectedNodeId) {
        incoming.add(edge.source);
        connectedEdgeIds.add(edge.id);
      }
      if (edge.source === selectedNodeId) {
        outgoing.add(edge.target);
        connectedEdgeIds.add(edge.id);
      }
    }

    const highlightedNodes: Node[] = baseNodes.map((node) => {
      if (!realNodeIds.has(node.id)) {
        return node;
      }
      const isSelected = node.id === selectedNodeId;
      const isIncoming = incoming.has(node.id);
      const isOutgoing = outgoing.has(node.id);
      const glowColor = isSelected
        ? SELECTED_COLOR
        : isIncoming
          ? INCOMING_COLOR
          : isOutgoing
            ? OUTGOING_COLOR
            : undefined;
      return {
        ...node,
        selected: isSelected,
        zIndex: glowColor ? 10 : 0,
        style: {
          ...node.style,
          opacity: glowColor ? 1 : FADED_OPACITY,
          border: glowColor ? `2px solid ${glowColor}` : node.style?.border,
          boxShadow: glowColor
            ? `0 0 0 2px ${wash(glowColor, 45)}, 0 0 16px 3px ${wash(glowColor, 55)}`
            : undefined,
        },
      };
    });

    const highlightedEdges: Edge[] = baseEdges.map((edge) => {
      const isIncomingEdge = edge.target === selectedNodeId;
      const isOutgoingEdge = edge.source === selectedNodeId;
      const isConnected = isIncomingEdge || isOutgoingEdge;
      const color = isIncomingEdge ? INCOMING_COLOR : isOutgoingEdge ? OUTGOING_COLOR : undefined;
      return {
        ...edge,
        selected: isConnected,
        zIndex: isConnected ? 10 : 0,
        style: {
          ...edge.style,
          stroke: color ?? edge.style?.stroke,
          opacity: isConnected ? 1 : FADED_OPACITY,
          strokeWidth: isConnected ? 2.5 : 1.5,
        },
        markerEnd: { type: MarkerType.ArrowClosed, color: color ?? "var(--gf-graph-edge)" },
        labelStyle: { ...edge.labelStyle, opacity: isConnected ? 1 : FADED_OPACITY },
      };
    });

    return { nodes: highlightedNodes, edges: highlightedEdges };
  }, [baseNodes, baseEdges, selectedNodeId, realNodeIds]);

  return (
    // Viewport-relative rather than a fixed 480px: a graph is the primary
    // content of the surface it sits on, and a hardcoded height gave a
    // 6-node graph and a 2,000-node graph exactly the same window — while
    // wasting most of a tall desktop display. Clamped so it stays usable on
    // a short laptop viewport and never grows past a comfortable reading
    // size on a very tall one.
    <div className="h-[clamp(20rem,70vh,45rem)] overflow-hidden rounded-xl border border-line bg-graph-canvas">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        fitView
        proOptions={{ hideAttribution: true }}
        colorMode={theme.mode}
        // Mounts only nodes/edges intersecting the viewport instead of the
        // full loaded set — the single biggest lever available without a
        // renderer swap (see the Architecture Page Scale Redesign doc,
        // §3): past a few hundred nodes, React Flow's per-node DOM/
        // ResizeObserver cost otherwise dominates regardless of how fast
        // the layout algorithm itself was.
        onlyRenderVisibleElements
        onNodeClick={(_, node) => {
          if (!realNodeIds.has(node.id)) return;
          setSelectedNodeId((current) => (current === node.id ? null : node.id));
        }}
        onPaneClick={() => setSelectedNodeId(null)}
      >
        <Background />
        <Controls />
        <MiniMap
          pannable
          zoomable
          style={{ background: "var(--gf-surface-raised)" }}
          maskColor="var(--gf-graph-cluster)"
        />
      </ReactFlow>
    </div>
  );
}
