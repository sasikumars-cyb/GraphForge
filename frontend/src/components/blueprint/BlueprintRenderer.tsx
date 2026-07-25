/**
 * BlueprintRenderer — dispatches a Diagram to the appropriate renderer.
 *
 * Architecture:
 *   BlueprintRenderer
 *     ├─ FlowGraphRenderer  (flow | architecture | sequence | dependency)
 *     ├─ TimelineRenderer   (timeline)
 *     └─ RiskHeatmapRenderer (risk_heatmap)
 *
 * Adding a new DiagramType: add a renderer function, add a case in the
 * switch at the bottom. Zero other changes required.
 *
 * Uses ReactFlow + dagre (already installed for DependencyGraph) for all
 * graph-based diagram types — no Mermaid, no external CDN.
 */

import { useMemo, useState, useCallback } from "react";
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
import type { Diagram, DiagramNode, NodeType } from "../../types/blueprint";

// ---------------------------------------------------------------------------
// Styling constants — node type → colour pair
// ---------------------------------------------------------------------------

const NODE_STYLES: Record<
  NodeType | "default",
  { bg: string; border: string; text: string }
> = {
  default:   { bg: "#1e293b", border: "#475569", text: "#e2e8f0" },
  input:     { bg: "#0c2744", border: "#38bdf8", text: "#bae6fd" },
  output:    { bg: "#052e16", border: "#34d399", text: "#a7f3d0" },
  component: { bg: "#1e293b", border: "#818cf8", text: "#e0e7ff" },
  topic:     { bg: "#2e1065", border: "#e879f9", text: "#f0abfc" },
  risk:      { bg: "#1c0a00", border: "#fb923c", text: "#fed7aa" },
  phase:     { bg: "#0c2744", border: "#60a5fa", text: "#bfdbfe" },
  entity:    { bg: "#1e1b4b", border: "#a5b4fc", text: "#e0e7ff" },
};

const RISK_SEVERITY_COLORS: Record<string, { bg: string; border: string; text: string; badge: string }> = {
  critical: { bg: "#450a0a", border: "#ef4444", text: "#fecaca", badge: "#ef4444" },
  high:     { bg: "#431407", border: "#f97316", text: "#fed7aa", badge: "#f97316" },
  medium:   { bg: "#422006", border: "#f59e0b", text: "#fde68a", badge: "#f59e0b" },
  low:      { bg: "#052e16", border: "#22c55e", text: "#bbf7d0", badge: "#22c55e" },
};

const NODE_W = 200;
const NODE_H = 60;

// ---------------------------------------------------------------------------
// dagre layout helper — with grid fallback and collision detection
// ---------------------------------------------------------------------------

function gridFallbackLayout(
  nodes: DiagramNode[],
  direction: string,
): Map<string, { x: number; y: number }> {
  const positions = new Map<string, { x: number; y: number }>();
  const isVertical = direction === "TB" || direction === "BT";
  const perRow = isVertical ? 1 : Math.max(1, Math.ceil(Math.sqrt(nodes.length)));
  const gapX = NODE_W + 90;
  const gapY = NODE_H + 110;
  nodes.forEach((n, i) => {
    const col = isVertical ? 0 : i % perRow;
    const row = isVertical ? i : Math.floor(i / perRow);
    positions.set(n.id, { x: col * gapX, y: row * gapY });
  });
  return positions;
}

function layoutDiagram(
  nodes: DiagramNode[],
  edges: Diagram["edges"],
  direction: string = "LR",
): Map<string, { x: number; y: number }> {
  const nodeIds = new Set(nodes.map((n) => n.id));
  // Strip edges that reference non-existent nodes before handing to dagre
  const validEdges = edges.filter((e) => nodeIds.has(e.source) && nodeIds.has(e.target));

  try {
    const g = new dagre.graphlib.Graph();
    g.setGraph({ rankdir: direction, nodesep: 60, ranksep: 140 });
    g.setDefaultEdgeLabel(() => ({}));
    for (const n of nodes) g.setNode(n.id, { width: NODE_W, height: NODE_H });
    for (const e of validEdges) g.setEdge(e.source, e.target);
    dagre.layout(g);

    const positions = new Map<string, { x: number; y: number }>();
    for (const n of nodes) {
      const pos = g.node(n.id);
      if (!pos) continue;
      positions.set(n.id, { x: pos.x - NODE_W / 2, y: pos.y - NODE_H / 2 });
    }

    // If dagre missed any node, fall through to grid
    if (positions.size < nodes.length) return gridFallbackLayout(nodes, direction);

    // Detect coordinate collisions (dagre anomaly on some graph topologies)
    const occupied = new Map<string, string>();
    for (const [id, pos] of positions) {
      const key = `${Math.round(pos.x / 8)},${Math.round(pos.y / 8)}`;
      if (occupied.has(key)) return gridFallbackLayout(nodes, direction);
      occupied.set(key, id);
    }

    return positions;
  } catch {
    return gridFallbackLayout(nodes, direction);
  }
}

// ---------------------------------------------------------------------------
// FlowGraphRenderer — handles flow / architecture / dependency / sequence
// ---------------------------------------------------------------------------

function FlowGraphRenderer({ diagram }: { diagram: Diagram }) {
  const direction = diagram.layout?.direction ?? "LR";

  const { flowNodes, flowEdges } = useMemo(() => {
    // Deduplicate nodes by ID — backend slug collisions can produce duplicate IDs
    const seenIds = new Set<string>();
    const uniqueNodes = diagram.nodes.filter((n) => {
      if (seenIds.has(n.id)) return false;
      seenIds.add(n.id);
      return true;
    });
    // Only pass edges where both endpoints exist
    const nodeIds = new Set(uniqueNodes.map((n) => n.id));
    const uniqueEdges = diagram.edges.filter(
      (e) => nodeIds.has(e.source) && nodeIds.has(e.target),
    );

    const positions = layoutDiagram(uniqueNodes, uniqueEdges, direction);

    const flowNodes: Node[] = uniqueNodes.map((n) => {
      const pos = positions.get(n.id) ?? { x: 0, y: 0 };
      const style = NODE_STYLES[(n.type as NodeType) ?? "default"] ?? NODE_STYLES.default;
      return {
        id: n.id,
        position: pos,
        data: {
          label: (
            <div style={{ lineHeight: 1.3 }}>
              {n.type && n.type !== "default" && (
                <div style={{ fontSize: 9, opacity: 0.7, textTransform: "uppercase", letterSpacing: "0.05em" }}>
                  {n.type}
                </div>
              )}
              <div style={{ fontWeight: 600, fontSize: 11 }}>{n.label}</div>
              {Boolean(n.properties?.affected_component) && (
                <div style={{ fontSize: 9, opacity: 0.6 }}>
                  → {String(n.properties?.affected_component)}
                </div>
              )}
            </div>
          ),
        },
        style: {
          background: style.bg,
          border: `1.5px solid ${style.border}`,
          borderRadius: 8,
          color: style.text,
          width: NODE_W,
          padding: "6px 10px",
          fontSize: 12,
          cursor: "pointer",
          transition: "box-shadow 0.15s ease, border-color 0.15s ease, transform 0.1s ease",
        },
        sourcePosition: direction === "TB" ? Position.Bottom : Position.Right,
        targetPosition: direction === "TB" ? Position.Top : Position.Left,
      };
    });

    const flowEdges: Edge[] = uniqueEdges.map((e, i) => ({
      id: `${e.id}-${i}`,
      source: e.source,
      target: e.target,
      label: e.label || undefined,
      animated: e.type === "data_flow",
      markerEnd: { type: MarkerType.ArrowClosed, color: "#64748b" },
      style: { stroke: "#64748b", strokeWidth: 1.5 },
      labelStyle: { fill: "#94a3b8", fontSize: 10 },
      labelBgStyle: { fill: "#0f172a", fillOpacity: 0.8 },
    }));

    return { flowNodes, flowEdges };
  }, [diagram, direction]);

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [hoveredId, setHoveredId] = useState<string | null>(null);

  const { nodes, edges } = useMemo(() => {
    const incoming = new Set<string>();
    const outgoing = new Set<string>();
    if (selectedId) {
      for (const e of flowEdges) {
        if (e.target === selectedId) incoming.add(e.source as string);
        if (e.source === selectedId) outgoing.add(e.target as string);
      }
    }

    return {
      nodes: flowNodes.map((n) => {
        const isSel = n.id === selectedId;
        const isHov = n.id === hoveredId && !selectedId;
        const isIn = incoming.has(n.id);
        const isOut = outgoing.has(n.id);

        const glowColor = isSel
          ? "#facc15"
          : isIn
          ? "#34d399"
          : isOut
          ? "#38bdf8"
          : isHov
          ? "#818cf8"
          : null;

        const dimmed = selectedId && !isSel && !isIn && !isOut;

        return {
          ...n,
          zIndex: glowColor ? 10 : 0,
          style: {
            ...n.style,
            opacity: dimmed ? 0.2 : 1,
            border: glowColor
              ? `2px solid ${glowColor}`
              : n.style?.border,
            boxShadow: glowColor
              ? `0 0 ${isSel ? "14px" : "8px"} 2px ${glowColor}66`
              : undefined,
            transform: isHov && !selectedId ? "scale(1.03)" : undefined,
          },
        };
      }),
      edges: flowEdges.map((e) => {
        const isIn = e.target === selectedId;
        const isOut = e.source === selectedId;
        const color = isIn ? "#34d399" : isOut ? "#38bdf8" : null;
        const dimmed = selectedId && !isIn && !isOut;
        return {
          ...e,
          style: {
            ...e.style,
            stroke: color ?? "#64748b",
            opacity: dimmed ? 0.1 : 1,
          },
          markerEnd: { type: MarkerType.ArrowClosed, color: color ?? "#64748b" },
        };
      }),
    };
  }, [flowNodes, flowEdges, selectedId, hoveredId]);

  const onNodeClick = useCallback(
    (_: unknown, node: Node) => setSelectedId((cur) => (cur === node.id ? null : node.id)),
    [],
  );
  const onNodeMouseEnter = useCallback((_: unknown, node: Node) => setHoveredId(node.id), []);
  const onNodeMouseLeave = useCallback(() => setHoveredId(null), []);

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      fitView
      fitViewOptions={{ padding: 0.2, maxZoom: 1.2 }}
      minZoom={0.08}
      maxZoom={2}
      proOptions={{ hideAttribution: true }}
      colorMode="dark"
      onNodeClick={onNodeClick}
      onPaneClick={() => setSelectedId(null)}
      onNodeMouseEnter={onNodeMouseEnter}
      onNodeMouseLeave={onNodeMouseLeave}
    >
      <Background />
      <Controls />
      <MiniMap pannable zoomable style={{ background: "#0f172a" }} />
    </ReactFlow>
  );
}

// ---------------------------------------------------------------------------
// TimelineRenderer — horizontal phase track with smooth expand
// ---------------------------------------------------------------------------

function TimelineRenderer({ diagram }: { diagram: Diagram }) {
  const [expanded, setExpanded] = useState<string | null>(null);

  return (
    <div className="flex h-full flex-col justify-center gap-6 px-4 py-6">
      <div className="relative flex items-start gap-0">
        {diagram.nodes.map((node, i) => {
          const isLast = i === diagram.nodes.length - 1;
          const steps = (node.properties?.steps as string[]) ?? [];
          const comps = (node.properties?.components as string[]) ?? [];
          const isOpen = expanded === node.id;

          return (
            <div key={node.id} className="flex flex-1 flex-col items-center">
              {/* connector line */}
              <div className="flex w-full items-center">
                <div className={`h-0.5 flex-1 transition-colors duration-300 ${i === 0 ? "opacity-0" : "bg-blue-700"}`} />
                <button
                  type="button"
                  onClick={() => setExpanded(isOpen ? null : node.id)}
                  className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-full border-2 text-xs font-bold transition-all duration-200 ${
                    isOpen
                      ? "border-blue-400 bg-blue-500/20 text-blue-200 shadow-[0_0_12px_2px_rgba(96,165,250,0.3)]"
                      : "border-blue-500 bg-slate-900 text-blue-300 hover:border-blue-400 hover:bg-slate-800"
                  }`}
                  aria-label={`Phase ${i + 1}: ${node.label}`}
                >
                  {i + 1}
                </button>
                <div className={`h-0.5 flex-1 transition-colors duration-300 ${isLast ? "opacity-0" : "bg-blue-700"}`} />
              </div>

              {/* label */}
              <div className="mt-2 px-1 text-center">
                <p className="text-xs font-semibold text-blue-300">{node.label}</p>
                {Boolean(node.properties?.description) && (
                  <p className="mt-0.5 line-clamp-2 text-[10px] text-slate-400">
                    {String(node.properties?.description)}
                  </p>
                )}
              </div>

              {/* expandable step list — smooth height transition */}
              <div
                style={{
                  maxHeight: isOpen && steps.length > 0 ? "220px" : "0",
                  overflow: "hidden",
                  transition: "max-height 0.3s cubic-bezier(0.4, 0, 0.2, 1)",
                  marginTop: isOpen ? "0.75rem" : "0",
                  width: "100%",
                }}
              >
                <div className="rounded-lg border border-blue-500/20 bg-slate-900/80 p-2">
                  <ul className="space-y-1">
                    {steps.map((s, si) => (
                      <li key={si} className="flex items-start gap-1.5 text-[10px] text-slate-300">
                        <span className="mt-0.5 h-1 w-1 shrink-0 rounded-full bg-blue-400" />
                        {s}
                      </li>
                    ))}
                  </ul>
                  {comps.length > 0 && (
                    <div className="mt-1.5 flex flex-wrap gap-1">
                      {comps.map((c) => (
                        <span key={c} className="rounded bg-slate-800 px-1 py-0.5 text-[9px] text-slate-400">
                          {c}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            </div>
          );
        })}
      </div>

      <p className="text-center text-[10px] text-slate-600">
        Click a phase to expand its details
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// RiskHeatmapRenderer — severity-coded risk cards with hover lift
// ---------------------------------------------------------------------------

function RiskHeatmapRenderer({ diagram }: { diagram: Diagram }) {
  const bySeverity = useMemo(() => {
    const buckets: Record<string, typeof diagram.nodes> = {
      critical: [],
      high: [],
      medium: [],
      low: [],
    };
    for (const n of diagram.nodes) {
      const sev = String(n.metadata?.severity ?? "low");
      (buckets[sev] ?? buckets.low).push(n);
    }
    return buckets;
  }, [diagram.nodes]);

  const totalCount = diagram.nodes.length;

  return (
    <div className="flex h-full flex-col gap-3 overflow-auto p-3">
      <div className="flex flex-wrap items-center gap-2">
        {Object.entries(RISK_SEVERITY_COLORS).map(([sev, col]) => {
          const count = bySeverity[sev].length;
          if (count === 0) return null;
          return (
            <span
              key={sev}
              className="flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium"
              style={{ background: col.bg, border: `1px solid ${col.border}`, color: col.text }}
            >
              <span className="h-2 w-2 rounded-full" style={{ background: col.badge }} />
              {sev} · {count}
            </span>
          );
        })}
        <span className="ml-auto text-xs text-slate-500">{totalCount} risk{totalCount !== 1 ? "s" : ""}</span>
      </div>

      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        {diagram.nodes.map((node) => {
          const sev = String(node.metadata?.severity ?? "low");
          const col = RISK_SEVERITY_COLORS[sev] ?? RISK_SEVERITY_COLORS.low;
          return (
            <div
              key={node.id}
              className="rounded-lg p-3 text-sm"
              style={{
                background: col.bg,
                border: `1px solid ${col.border}`,
                color: col.text,
                transition: "transform 0.15s ease, box-shadow 0.15s ease",
                cursor: "default",
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.transform = "translateY(-2px)";
                e.currentTarget.style.boxShadow = `0 6px 16px ${col.border}44`;
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.transform = "";
                e.currentTarget.style.boxShadow = "";
              }}
            >
              <div
                className="mb-1.5 inline-block rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide"
                style={{ background: col.badge + "33", color: col.badge }}
              >
                {sev}
              </div>
              <p className="leading-snug">{node.label}</p>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Public: BlueprintRenderer dispatch
// ---------------------------------------------------------------------------

export interface BlueprintRendererProps {
  diagram: Diagram;
}

export function BlueprintRenderer({ diagram }: BlueprintRendererProps) {
  switch (diagram.type) {
    case "flow":
    case "architecture":
    case "sequence":
    case "dependency":
      return <FlowGraphRenderer diagram={diagram} />;
    case "er":
      return <FlowGraphRenderer diagram={diagram} />;
    case "timeline":
      return <TimelineRenderer diagram={diagram} />;
    case "risk_heatmap":
      return <RiskHeatmapRenderer diagram={diagram} />;
    default:
      return (
        <div className="flex h-full items-center justify-center text-sm text-slate-500">
          Renderer for <code className="mx-1 font-mono">{diagram.type}</code> not yet implemented.
        </div>
      );
  }
}
