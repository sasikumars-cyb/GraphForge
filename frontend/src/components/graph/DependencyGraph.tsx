import { useMemo } from "react";
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

const NODE_WIDTH = 190;
const NODE_HEIGHT = 56;

export const NODE_LABEL_COLORS: Record<string, { background: string; border: string }> = {
  Controller: { background: "#0c4a6e", border: "#38bdf8" },
  Service: { background: "#164e3c", border: "#34d399" },
  FeignClient: { background: "#3b2f0b", border: "#fbbf24" },
  Endpoint: { background: "#1e1b4b", border: "#818cf8" },
  KafkaTopic: { background: "#4a044e", border: "#e879f9" },
  MavenDependency: { background: "#292524", border: "#a8a29e" },
  Component: { background: "#1e293b", border: "#94a3b8" },
};

function primaryLabel(labels: string[]): string {
  return labels.find((label) => label in NODE_LABEL_COLORS) ?? "Component";
}

function layoutGraph(graph: Graph): { nodes: Node[]; edges: Edge[] } {
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

  const nodes: Node[] = graph.nodes.map((node) => {
    const position = g.node(node.id);
    const label = primaryLabel(node.labels);
    const colors = NODE_LABEL_COLORS[label];
    const name = String(node.properties.name ?? node.id);
    return {
      id: node.id,
      position: { x: position.x - NODE_WIDTH / 2, y: position.y - NODE_HEIGHT / 2 },
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
        color: "#e2e8f0",
        fontSize: 12,
        padding: 8,
      },
      sourcePosition: Position.Right,
      targetPosition: Position.Left,
    };
  });

  const edges: Edge[] = graph.edges.map((edge, index) => ({
    id: `${edge.source_id}->${edge.target_id}-${edge.type}-${index}`,
    source: edge.source_id,
    target: edge.target_id,
    label: edge.type,
    animated: edge.type === "PRODUCES_TO" || edge.type === "CONSUMES_FROM",
    markerEnd: { type: MarkerType.ArrowClosed, color: "#64748b" },
    style: { stroke: "#64748b" },
    labelStyle: { fill: "#94a3b8", fontSize: 10 },
  }));

  return { nodes, edges };
}

interface DependencyGraphProps {
  graph: Graph;
}

export function DependencyGraph({ graph }: DependencyGraphProps) {
  const { nodes, edges } = useMemo(() => layoutGraph(graph), [graph]);

  return (
    <div style={{ height: 480 }} className="overflow-hidden rounded-lg border border-slate-700">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        fitView
        proOptions={{ hideAttribution: true }}
        colorMode="dark"
      >
        <Background />
        <Controls />
        <MiniMap pannable zoomable style={{ background: "#0f172a" }} />
      </ReactFlow>
    </div>
  );
}
