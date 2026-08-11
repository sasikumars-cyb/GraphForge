/**
 * Neo4j edge-type constants ("DEPENDS_ON_REPOSITORY", "SHARES_TOPIC",
 * "CALLS_SERVICE", ...) shown verbatim as user-facing text — the graph
 * schema's own internal vocabulary leaking into the UI (UX audit P2.2).
 * The raw constant is still what Evidence/JSON/technical-detail views show
 * (never renamed there — that's the accurate wire value); this is only for
 * text a normal user reads directly, e.g. a relationship badge in a graph
 * or dependency list.
 *
 * Deliberately a lookup, not a generic transform (e.g.
 * `type.replaceAll("_", " ").toLowerCase()`) — "DEPENDS_ON" -> "depends
 * on" is fine either way, but "CALLS_SERVICE" -> "calls service" reads
 * worse than the hand-written "calls", and a generic transform can't know
 * that. Falls back to the generic transform for any type this list
 * doesn't yet name, so an unrecognized/future edge type still reads as
 * words instead of not rendering at all.
 */
const RELATIONSHIP_LABELS: Record<string, string> = {
  DEPENDS_ON: "depends on",
  DEPENDS_ON_REPOSITORY: "depends on",
  CALLS_SERVICE: "calls",
  CALLS: "calls",
  SHARES_TOPIC: "shares a topic with",
  PRODUCES_TO: "produces to",
  CONSUMES_FROM: "consumes from",
  CONTAINS: "contains",
  IMPORTS: "imports",
};

export function humanizeRelationship(type: string): string {
  return RELATIONSHIP_LABELS[type] ?? type.replaceAll("_", " ").toLowerCase();
}
