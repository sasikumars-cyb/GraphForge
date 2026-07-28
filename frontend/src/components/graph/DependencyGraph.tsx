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

// Node-focused highlighting palette (DependencyGraph only).
const SELECTED_COLOR = "#facc15"; // amber - the clicked node itself
const INCOMING_COLOR = "#34d399"; // emerald - nodes/edges pointing into the selection
const OUTGOING_COLOR = "#38bdf8"; // sky - nodes/edges the selection points out to
const FADED_OPACITY = 0.15;

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
    const name = String(node.properties.name ?? node.id);
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
    markerEnd: { type: MarkerType.ArrowClosed, color: "var(--gf-slate-500)" },
    style: { stroke: "var(--gf-slate-500)" },
    labelStyle: { fill: "var(--gf-slate-400)", fontSize: 10 },
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
        border: `1px solid ${colors.border}`,
        borderRadius: 8,
        width: NODE_WIDTH,
        color: "var(--gf-slate-200)",
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
      style: {
        width: box.maxX - box.minX + GROUP_PADDING * 2,
        height: box.maxY - box.minY + GROUP_PADDING * 2 + 20,
        background: "rgba(148, 163, 184, 0.04)",
        border: "1px dashed var(--gf-slate-600)",
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
        color: "var(--gf-slate-300)",
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
        border: `1px solid ${colors.border}`,
        borderRadius: 8,
        width: NODE_WIDTH,
        color: "var(--gf-slate-200)",
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
      background: "var(--gf-slate-800)",
      border: "1px solid var(--gf-slate-500)",
      borderRadius: 10,
      width: OVERVIEW_NODE_WIDTH,
      color: "var(--gf-slate-200)",
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
 * Directed edges between cards (producer -> consumer, per shared Kafka
 * topics) are drawn from `edges`, aggregated once from the same
 * `/cross-repository-links` data each expand already fetches - no extra
 * calls here, and none at all on hover.
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
        <span title={`Shared topics:\n${edge.topics.join("\n")}`}>{edge.topics.length}</span>
      ),
      markerEnd: { type: MarkerType.ArrowClosed, color: "var(--gf-slate-500)" },
      style: { stroke: "var(--gf-slate-500)" },
      labelBgStyle: { fill: "var(--gf-slate-800)" },
      labelStyle: { fill: "var(--gf-slate-200)", fontSize: 11 },
    }));

    return { nodes: laidOutNodes, edges: flowEdges };
  }, [repositories, dependencyEdges]);

  return (
    <div style={{ height: 480 }} className="overflow-hidden rounded-lg border border-slate-700">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        fitView
        proOptions={{ hideAttribution: true }}
        colorMode={theme.mode}
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
          boxShadow: glowColor ? `0 0 0 2px ${glowColor}55, 0 0 16px 3px ${glowColor}88` : undefined,
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
          strokeWidth: isConnected ? 2.5 : 1,
        },
        markerEnd: { type: MarkerType.ArrowClosed, color: color ?? "var(--gf-slate-500)" },
        labelStyle: { ...edge.labelStyle, opacity: isConnected ? 1 : FADED_OPACITY },
      };
    });

    return { nodes: highlightedNodes, edges: highlightedEdges };
  }, [baseNodes, baseEdges, selectedNodeId, realNodeIds]);

  return (
    <div style={{ height: 480 }} className="overflow-hidden rounded-lg border border-slate-700">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        fitView
        proOptions={{ hideAttribution: true }}
        colorMode={theme.mode}
        onNodeClick={(_, node) => {
          if (!realNodeIds.has(node.id)) return;
          setSelectedNodeId((current) => (current === node.id ? null : node.id));
        }}
        onPaneClick={() => setSelectedNodeId(null)}
      >
        <Background />
        <Controls />
        <MiniMap pannable zoomable style={{ background: "var(--gf-slate-900)" }} />
      </ReactFlow>
    </div>
  );
}
