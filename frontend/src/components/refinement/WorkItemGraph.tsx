import { useMemo } from "react";
import { ReactFlow, Background, Controls, MarkerType, Position, type Node, type Edge } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import dagre from "@dagrejs/dagre";
import type { EdgeRelationship, RefinementPlan, WorkItemType } from "../../types/conversation";

const NODE_WIDTH = 200;
const NODE_HEIGHT = 60;

/** Type distinguished by color + label text, never color alone — a
 * dashed border further marks "proposed" independent of color, so the
 * existing/proposed distinction survives even for a colorblind reader or
 * a black-and-white projector. */
const TYPE_STYLE: Record<
  WorkItemType,
  { bg: string; border: string; text: string; label: string }
> = {
  epic: {
    bg: "var(--gf-accent-bg)",
    border: "var(--gf-accent-line)",
    text: "var(--gf-accent-fg)",
    label: "EPIC",
  },
  story: {
    bg: "var(--gf-info-bg)",
    border: "var(--gf-info-line)",
    text: "var(--gf-info-fg)",
    label: "STORY",
  },
  task: {
    bg: "var(--gf-neutral-bg)",
    border: "var(--gf-line-strong)",
    text: "var(--gf-fg-secondary)",
    label: "TASK",
  },
  spike: {
    bg: "var(--gf-warning-bg)",
    border: "var(--gf-warning-line)",
    text: "var(--gf-warning-fg)",
    label: "SPIKE",
  },
};

const RELATIONSHIP_STYLE: Record<EdgeRelationship, { stroke: string; dash?: string }> = {
  blocks: { stroke: "var(--gf-danger-line)" },
  depends_on: { stroke: "var(--gf-info-line)" },
  enables: { stroke: "var(--gf-success-line)", dash: "6 3" },
  related: { stroke: "var(--gf-graph-edge)", dash: "2 3" },
  parent_child: { stroke: "var(--gf-line-strong)" },
};

function layout(plan: RefinementPlan): { nodes: Node[]; edges: Edge[] } {
  const g = new dagre.graphlib.Graph();
  // Tighter than the Architecture graph's own dagre spacing — a
  // refinement plan is a handful of work items, not dozens of repository
  // nodes, and a wide rank spread was pushing `fitView` past its
  // readability floor on an ordinary requirement (5-6 rank-deep chains
  // are common once a spike->producer->consumer->... sequence forms).
  g.setGraph({ rankdir: "LR", nodesep: 24, ranksep: 64 });
  g.setDefaultEdgeLabel(() => ({}));
  for (const item of plan.work_items) g.setNode(item.id, { width: NODE_WIDTH, height: NODE_HEIGHT });
  for (const edge of plan.edges) {
    if (g.hasNode(edge.source_id) && g.hasNode(edge.target_id)) {
      g.setEdge(edge.source_id, edge.target_id);
    }
  }
  dagre.layout(g);

  const criticalIds = new Set(plan.critical_paths.flat());

  const nodes: Node[] = plan.work_items.map((item) => {
    const position = g.node(item.id) ?? { x: 0, y: 0 };
    const style = TYPE_STYLE[item.type];
    const isCritical = criticalIds.has(item.id);
    return {
      id: item.id,
      position: { x: position.x - NODE_WIDTH / 2, y: position.y - NODE_HEIGHT / 2 },
      width: NODE_WIDTH,
      height: NODE_HEIGHT,
      data: {
        label: (
          <div>
            <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, fontWeight: 600, opacity: 0.75, letterSpacing: 0.3 }}>
              <span>{style.label}</span>
              <span>{item.status === "proposed" ? "PROPOSED" : item.id}</span>
            </div>
            <div style={{ fontWeight: 600, fontSize: 13, marginTop: 3, lineHeight: 1.3 }}>
              {item.title}
            </div>
          </div>
        ),
      },
      style: {
        background: style.bg,
        border: `${isCritical ? 2 : 1}px ${item.status === "proposed" ? "dashed" : "solid"} ${style.border}`,
        borderRadius: 10,
        color: style.text,
        padding: 8,
        width: NODE_WIDTH,
      },
      sourcePosition: Position.Right,
      targetPosition: Position.Left,
    };
  });

  const edges: Edge[] = plan.edges.map((edge, index) => {
    const relStyle = RELATIONSHIP_STYLE[edge.relationship];
    return {
      id: `${edge.source_id}->${edge.target_id}-${edge.relationship}-${index}`,
      source: edge.source_id,
      target: edge.target_id,
      label: edge.relationship.replace("_", " "),
      markerEnd: { type: MarkerType.ArrowClosed, color: relStyle.stroke },
      style: { stroke: relStyle.stroke, strokeWidth: 1.5, strokeDasharray: relStyle.dash },
      labelStyle: { fill: "var(--gf-graph-edge-label)", fontSize: 11, fontWeight: 500 },
      labelBgStyle: { fill: "var(--gf-graph-canvas)", fillOpacity: 0.85 },
    };
  });

  return { nodes, edges };
}

/**
 * The interactive work-item dependency map — same visual grammar
 * (`@xyflow/react` + `@dagrejs/dagre` LR layout, the same selection wash
 * colors) `DependencyGraph`/`BlastRadiusGraph` already established for
 * the Architecture graph, adapted for work items instead of repository
 * nodes. Selecting a node dims everything not directly connected to it
 * and washes incoming/outgoing edges in the same warning/info tones the
 * Architecture graph already uses for "what depends on this" vs. "what
 * this depends on" — so "Selected → Dependencies → Downstream impact"
 * reads the same way it does everywhere else in GraphForge.
 */
export function WorkItemGraph({
  plan,
  selectedId,
  onSelect,
}: {
  plan: RefinementPlan;
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  const { nodes: baseNodes, edges: baseEdges } = useMemo(() => layout(plan), [plan]);

  const { nodes, edges } = useMemo(() => {
    if (!selectedId) return { nodes: baseNodes, edges: baseEdges };
    const incoming = new Set(
      plan.edges.filter((e) => e.target_id === selectedId).map((e) => e.source_id),
    );
    const outgoing = new Set(
      plan.edges.filter((e) => e.source_id === selectedId).map((e) => e.target_id),
    );
    const highlightedNodes = applyHighlight(baseNodes, selectedId, incoming, outgoing);
    const highlightedEdges = baseEdges.map((edge) => {
      const related = edge.source === selectedId || edge.target === selectedId;
      return related ? edge : { ...edge, style: { ...edge.style, opacity: 0.15 } };
    });
    return { nodes: highlightedNodes, edges: highlightedEdges };
  }, [baseNodes, baseEdges, selectedId, plan.edges]);

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      onNodeClick={(_, node) => onSelect(node.id)}
      fitView
      fitViewOptions={{ padding: 0.25, maxZoom: 1 }}
      // A floor on how far fitView is allowed to shrink the canvas to —
      // without it, a small plan in a short container (see
      // RefinementGraphPage's own comment on why the container's height
      // isn't guaranteed) could zoom labels down past legibility. Below
      // this floor a large plan simply doesn't fully fit at once, which
      // panning/the zoom controls handle; unreadable text has no
      // equivalent fallback.
      minZoom={0.7}
      maxZoom={1.5}
      proOptions={{ hideAttribution: true }}
    >
      <Background color="var(--gf-line-muted)" gap={20} />
      <Controls showInteractive={false} />
    </ReactFlow>
  );
}

function applyHighlight(
  baseNodes: Node[],
  selectedId: string,
  incoming: Set<string>,
  outgoing: Set<string>,
): Node[] {
  return baseNodes.map((node) => {
    if (node.id === selectedId) {
      // The product's own Iris accent — `--gf-accent-solid`, the same
      // token every primary button/link uses — not `--gf-graph-
      // selected` (a lighter shade the Architecture graph's own dark
      // canvas uses for contrast, close in hue but visibly a different
      // color next to this page's buttons). Selecting a node should
      // read as exactly the same "this is what you picked" color as
      // everywhere else in GraphForge, not a graph-specific variant.
      return { ...node, style: { ...node.style, boxShadow: "0 0 0 2px var(--gf-accent-solid)" } };
    }
    if (incoming.has(node.id)) {
      return { ...node, style: { ...node.style, boxShadow: "0 0 0 2px var(--gf-graph-incoming)" } };
    }
    if (outgoing.has(node.id)) {
      return { ...node, style: { ...node.style, boxShadow: "0 0 0 2px var(--gf-graph-outgoing)" } };
    }
    return { ...node, style: { ...node.style, opacity: 0.35 } };
  });
}
