/**
 * How a Knowledge Graph node's labels become a display name and a colour.
 *
 * Kept out of DependencyGraph.tsx so these stay pure, directly testable, and
 * don't break that file's fast-refresh boundary (a component module should
 * only export components).
 *
 * Colours are returned as `var(--gf-node-N-*)` references rather than literal
 * hexes. Two reasons:
 *   1. The previous map hardcoded dark-only hexes (`#0c4a6e` on a node whose
 *      label text was `var(--gf-slate-200)`). In the light themes that
 *      resolved to dark-navy-on-dark-slate — about 1.3:1, i.e. invisible —
 *      which is the "black text on dark backgrounds" the graph was showing.
 *   2. Inline `var()` in a ReactFlow node style re-resolves on theme switch
 *      with no React work at all, so the graph retints itself for free.
 *
 * The eight node slots are a *categorical* palette: fixed assignment order,
 * never cycled. A label with no slot gets the neutral treatment rather than
 * a generated ninth hue, so two unrelated types can never accidentally read
 * as the same category. Each slot's steps are validated per theme for
 * colour-vision-deficiency separation and >= 4.5:1 label contrast on the
 * node's own fill (see src/styles/tokens.css).
 */

export interface NodeColors {
  background: string;
  border: string;
  /** Label ink — must be read from the same slot as the fill it sits on. */
  text: string;
}

function slot(n: number): NodeColors {
  return {
    background: `var(--gf-node-${n}-bg)`,
    border: `var(--gf-node-${n}-line)`,
    text: `var(--gf-node-${n}-fg)`,
  };
}

/** Fallback styling for a node label we have no explicit slot for. */
export const UNKNOWN_LABEL_COLORS: NodeColors = {
  background: "var(--gf-surface-raised)",
  border: "var(--gf-line-strong)",
  text: "var(--gf-fg-secondary)",
};

/**
 * Explicit colours for the node labels we know about.
 *
 * This map is a *preference*, not the set of renderable labels — see
 * `resolveLabelColors`. Keying rendering directly off it meant every label
 * it didn't list (`Module`, `Class`, `Function`, `PythonDependency` — i.e.
 * the entire output of the Python indexer) collapsed onto the generic
 * `Component` entry and drew in identical grey, while the legend advertised
 * six Java/Spring types the graph could not contain.
 */
export const NODE_LABEL_COLORS: Record<string, NodeColors> = {
  // Java / Spring Boot
  Controller: slot(1), // entry points share the blue slot with Module
  Service: slot(2), // behaviour shares green with Function
  FeignClient: slot(4), // outbound integration
  Endpoint: slot(5),
  KafkaTopic: slot(3), // messaging
  MavenDependency: UNKNOWN_LABEL_COLORS, // external, deliberately recessive
  // Python
  Module: slot(1),
  Class: slot(7),
  Function: slot(2),
  PythonDependency: UNKNOWN_LABEL_COLORS,
  // Cross-language
  Repository: slot(6),
  Component: UNKNOWN_LABEL_COLORS,
};

/**
 * Structural labels every node carries. They describe how the node is
 * stored, not what it is, so they must never win when choosing what to
 * display: a Python function arrives as `["Component","Function","GraphNode"]`
 * and should read as "Function", not "Component".
 */
const STRUCTURAL_LABELS = new Set(["GraphNode", "Component"]);

/**
 * The most specific label on a node: prefer a non-structural one, and only
 * fall back to a structural label when that's genuinely all there is.
 * Returns whatever the indexer actually wrote, so a language added later
 * renders under its own name rather than silently as "Component".
 */
export function primaryLabel(labels: string[]): string {
  return (
    labels.find((label) => !STRUCTURAL_LABELS.has(label) && label in NODE_LABEL_COLORS) ??
    labels.find((label) => !STRUCTURAL_LABELS.has(label)) ??
    labels.find((label) => label in NODE_LABEL_COLORS) ??
    "Component"
  );
}

export function resolveLabelColors(label: string): NodeColors {
  return NODE_LABEL_COLORS[label] ?? UNKNOWN_LABEL_COLORS;
}

/**
 * The labels actually present in a graph, most frequent first — what the
 * legend should list. Built from the data so the legend can never advertise
 * a type this graph doesn't contain, nor omit one it does.
 */
export function legendLabelsFor(nodes: { labels: string[] }[]): string[] {
  const counts = new Map<string, number>();
  for (const node of nodes) {
    const label = primaryLabel(node.labels);
    counts.set(label, (counts.get(label) ?? 0) + 1);
  }
  return [...counts.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .map(([label]) => label);
}
