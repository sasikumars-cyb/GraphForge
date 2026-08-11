import { useMemo } from "react";
import { ReactFlow, Background, Controls, MarkerType, type Node, type Edge } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { primaryLabel, resolveLabelColors } from "../graph/graphLabels";
import { humanizeRelationship } from "../../lib/humanizeRelationship";
import { useTheme } from "../../theme/theme-context";
import type { Graph, GraphNode as GraphNodeType } from "../../types/graph";

const RING_SPACING = 170;
const NODE_SIZE = 88;
const SEED_NODE_SIZE = 104;

function hopDistanceOf(node: GraphNodeType): number {
  const raw = node.properties.hop_distance;
  return typeof raw === "number" ? raw : 0;
}

/** Concentric ring guides — rendered as non-interactive React Flow "group"
 * nodes (the same technique `DependencyGraph` already uses for its
 * multi-repository clustering boxes, made circular instead of
 * rectangular) so they pan/zoom together with the real nodes rather than
 * needing separate coordinate-system math. This is what makes hop
 * distance readable as *distance from center* at a glance, not just
 * inferrable from node position alone. */
function ringGuides(maxHop: number): Node[] {
  const rings: Node[] = [];
  for (let hop = 1; hop <= maxHop; hop++) {
    const radius = hop * RING_SPACING;
    rings.push({
      id: `ring:${hop}`,
      type: "group",
      position: { x: -radius, y: -radius },
      width: radius * 2,
      height: radius * 2,
      style: {
        width: radius * 2,
        height: radius * 2,
        borderRadius: "50%",
        border: "1px dashed var(--gf-line-muted)",
        background: "transparent",
      },
      data: {},
      selectable: false,
      draggable: false,
      connectable: false,
      zIndex: -10,
    });
    rings.push({
      id: `ring-label:${hop}`,
      type: "default",
      position: { x: 6, y: -radius - 2 },
      data: { label: `${hop} hop${hop === 1 ? "" : "s"}` },
      style: {
        background: "transparent",
        border: "none",
        color: "var(--gf-fg-subtle)",
        fontSize: 10,
        padding: 0,
        width: "auto",
      },
      selectable: false,
      draggable: false,
      connectable: false,
      zIndex: -9,
    });
  }
  return rings;
}

/** Radial layout: hop distance (already computed server-side by
 * `get_neighborhood`, carried in each node's own `properties.hop_distance`
 * — see backend/app/graph/neo4j_repository.py) determines *which ring* a
 * node sits on; nodes on the same ring are spread evenly around it. This
 * is a genuinely different layout algorithm from `DependencyGraph`'s
 * dagre layered layout — hop-distance-from-a-seed has no natural "flows
 * left to right" reading, but has an extremely natural "how far from the
 * center" one, which a ring distance communicates in the first glance a
 * layered graph wouldn't. */
function layoutRadial(graph: Graph): { nodes: Node[]; edges: Edge[] } {
  const byHop = new Map<number, GraphNodeType[]>();
  for (const node of graph.nodes) {
    const hop = hopDistanceOf(node);
    const bucket = byHop.get(hop) ?? [];
    bucket.push(node);
    byHop.set(hop, bucket);
  }
  const maxHop = Math.max(0, ...byHop.keys());

  const realNodes: Node[] = [];
  for (const [hop, ringNodes] of byHop) {
    const isSeed = hop === 0;
    const size = isSeed ? SEED_NODE_SIZE : NODE_SIZE;
    const radius = hop * RING_SPACING;
    const angleStep = (2 * Math.PI) / ringNodes.length;
    ringNodes.forEach((node, i) => {
      // Start at the top (-90deg) so the first node on any ring reads
      // top-down, not from the 3-o'clock position atan2 defaults to.
      const angle = ringNodes.length === 1 ? -Math.PI / 2 : i * angleStep - Math.PI / 2;
      const x = radius * Math.cos(angle) - size / 2;
      const y = radius * Math.sin(angle) - size / 2;
      const label = primaryLabel(node.labels);
      const colors = resolveLabelColors(label);
      const name = String(node.properties.name ?? node.id);
      realNodes.push({
        id: node.id,
        position: { x, y },
        width: size,
        height: size,
        data: {
          label: (
            <div style={{ textAlign: "center", lineHeight: 1.2 }}>
              <div style={{ fontSize: 9, opacity: 0.75 }}>{label}</div>
              <div style={{ fontWeight: 600, fontSize: 11, wordBreak: "break-word" }}>{name}</div>
            </div>
          ),
        },
        style: {
          background: colors.background,
          border: `${isSeed ? 3 : 1}px solid ${isSeed ? "var(--gf-graph-selected)" : colors.border}`,
          borderRadius: "50%",
          width: size,
          height: size,
          color: colors.text,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          padding: 6,
        },
      });
    });
  }

  const edges: Edge[] = graph.edges.map((edge, index) => ({
    id: `${edge.source_id}->${edge.target_id}-${edge.type}-${index}`,
    source: edge.source_id,
    target: edge.target_id,
    label: humanizeRelationship(edge.type),
    markerEnd: { type: MarkerType.ArrowClosed, color: "var(--gf-graph-edge)" },
    style: { stroke: "var(--gf-graph-edge)", strokeWidth: 1.5 },
    labelStyle: { fill: "var(--gf-graph-edge-label)", fontSize: 9 },
  }));

  return { nodes: [...ringGuides(maxHop), ...realNodes], edges };
}

/** The Impact lens's signature visualization (ARCHITECTURE_EXPERIENCE_
 * REDESIGN.md): a radial blast-radius graph, hop-distance-from-seed as
 * ring position — "how directly is this affected" readable from shape
 * alone, in the 5-10 second sense that document argues every lens should
 * hit before any list/table is read. */
export function BlastRadiusGraph({
  graph,
  seedNodeId,
  onNodeSelect,
  selectedNodeId,
}: {
  graph: Graph;
  seedNodeId: string;
  onNodeSelect?: (node: GraphNodeType | null) => void;
  selectedNodeId?: string | null;
}) {
  const { theme } = useTheme();
  // `seedNodeId` isn't read by the layout itself (hop_distance === 0
  // already identifies the seed, by construction of `get_neighborhood`)
  // — kept as an explicit prop so a caller's intent is documented at the
  // call site, and available for a future accessible label/announcement.
  const { nodes, edges } = useMemo(() => layoutRadial(graph), [graph]);
  const nodesById = useMemo(() => new Map(graph.nodes.map((n) => [n.id, n])), [graph]);

  const styledNodes = useMemo(
    () =>
      nodes.map((node) =>
        node.id === selectedNodeId
          ? {
              ...node,
              zIndex: 20,
              style: {
                ...node.style,
                boxShadow: "0 0 0 3px var(--gf-graph-selected), 0 0 18px 4px var(--gf-graph-selected)",
              },
            }
          : node,
      ),
    [nodes, selectedNodeId],
  );

  return (
    // `role="img"` deliberately avoided here — unlike a static diagram,
    // this graph is interactive (pan/zoom/click), and ARIA's `img` role
    // would hide those descendants from assistive tech entirely.
    // `data-seed-node-id` keeps the seed identity attached to the DOM for
    // debugging/testing without affecting accessibility semantics.
    <div
      data-seed-node-id={seedNodeId}
      className="h-[clamp(24rem,70vh,45rem)] overflow-hidden rounded-xl border border-line bg-graph-canvas"
    >
      <ReactFlow
        nodes={styledNodes}
        edges={edges}
        fitView
        proOptions={{ hideAttribution: true }}
        colorMode={theme.mode}
        onlyRenderVisibleElements
        onNodeClick={(_, node) => {
          const realNode = nodesById.get(node.id);
          if (!realNode) return; // ring guide, not a real node
          onNodeSelect?.(realNode.id === selectedNodeId ? null : realNode);
        }}
        onPaneClick={() => onNodeSelect?.(null)}
      >
        <Background />
        <Controls />
      </ReactFlow>
    </div>
  );
}
