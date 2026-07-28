/**
 * How a Knowledge Graph node's labels become a display name and a colour.
 *
 * Kept out of DependencyGraph.tsx so these stay pure, directly testable, and
 * don't break that file's fast-refresh boundary (a component module should
 * only export components).
 */

/** Fallback styling for a node label we have no explicit colour for. */
export const UNKNOWN_LABEL_COLORS = { background: "#1e293b", border: "#94a3b8" };

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
export const NODE_LABEL_COLORS: Record<string, { background: string; border: string }> = {
  // Java / Spring Boot
  Controller: { background: "#0c4a6e", border: "#38bdf8" },
  Service: { background: "#164e3c", border: "#34d399" },
  FeignClient: { background: "#3b2f0b", border: "#fbbf24" },
  Endpoint: { background: "#1e1b4b", border: "#818cf8" },
  KafkaTopic: { background: "#4a044e", border: "#e879f9" },
  MavenDependency: { background: "#292524", border: "#a8a29e" },
  // Python
  Module: { background: "#0c3f5e", border: "#5eb0ef" },
  Class: { background: "#3d1f4d", border: "#c084fc" },
  Function: { background: "#14392e", border: "#5eead4" },
  PythonDependency: { background: "#292524", border: "#a8a29e" },
  // Cross-language
  Repository: { background: "#422006", border: "#fb923c" },
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

export function resolveLabelColors(label: string): { background: string; border: string } {
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
