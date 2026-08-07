/**
 * Node-type -> architecture tier, for the Architecture lens's layer bands
 * (ARCHITECTURE_EXPERIENCE_REDESIGN.md: "horizontal bands (service / data
 * / code), not a flat mixed-type force layout"). Kept out of
 * DependencyGraph.tsx for the same fast-refresh-boundary reason
 * graphLabels.ts already documents, and pure/independently testable for
 * the same reason that file is.
 *
 * Exactly three tiers, matching the design doc's own three-way split —
 * "code" is the catch-all default rather than a fourth "other" bucket, so
 * every node always lands in exactly one of the three named bands (a
 * language added later renders in the tier its role implies, not in a
 * bucket nobody asked for).
 */

export type ArchitectureLayer = "service" | "data" | "code";

/** Top-to-bottom band order — matches the doc's own worked example
 * ("request flow left to right" happens *within* a band via the existing
 * dagre LR layout; band order top-to-bottom is what makes "the data layer
 * has direct edges from the service layer skipping the expected mid-tier"
 * a shape you see, not a fact you'd have to trace edges to find). */
export const LAYER_ORDER: readonly ArchitectureLayer[] = ["service", "code", "data"];

export const LAYER_LABELS: Record<ArchitectureLayer, string> = {
  service: "Service",
  code: "Code",
  data: "Data",
};

// Every entry-point / outbound-integration type the indexer currently
// produces (see NODE_LABEL_COLORS in graphLabels.ts for the same
// enumeration) — the "front door and edges" of a system. `Repository`
// (the per-repo hub node every graph has) is included here too: it's the
// entry point every other node in this repository hangs off of, so it
// belongs at the top of the stack, not floating unclassified.
const SERVICE_LABELS = new Set(["Controller", "Service", "Endpoint", "FeignClient", "Repository"]);

// Where data at rest or in flight lives — a table is data at rest, a
// topic is data in flight, both read as "the data layer" under the doc's
// three-tier model (it doesn't carve out a separate messaging tier).
const DATA_LABELS = new Set(["DataTable", "KafkaTopic"]);

/**
 * `label` is the already-resolved `primaryLabel(node.labels)` (see
 * graphLabels.ts) — this function doesn't re-derive it, so a node's tier
 * and its node-type badge/color always agree by construction.
 */
export function layerForLabel(label: string): ArchitectureLayer {
  if (SERVICE_LABELS.has(label)) return "service";
  if (DATA_LABELS.has(label)) return "data";
  // Everything else — Module/Class/Function/Component/MavenDependency/
  // PythonDependency, and any future label this map doesn't know about
  // yet — is source code or a code-level dependency on it. The catch-all
  // is deliberate: an unclassified label should read as "code", the tier
  // with the least severe consequence for being wrong, not vanish into an
  // "other" band nobody would think to look at.
  return "code";
}
