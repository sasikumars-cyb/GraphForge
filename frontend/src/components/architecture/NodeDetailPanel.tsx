import { useEffect, useMemo, useState } from "react";
import {
  ArrowDownToLine,
  ArrowUpFromLine,
  ChevronDown,
  Compass,
  Search,
  X,
} from "lucide-react";
import { RiskBadge } from "../RiskBadge";
import { ProvenanceTag } from "../intelligence/ProvenanceTag";
import { primaryLabel, resolveLabelColors } from "../graph/graphLabels";
import type { Graph, GraphEdge, GraphNode } from "../../types/graph";
import type { RiskLevel } from "../../types/domain";

const HIDDEN_PROPERTY_KEYS = new Set(["repository_id", "name"]);

function displayName(node: GraphNode): string {
  return String(node.properties.name ?? node.id);
}

/** Direct dependents (fan-in) are what actually determines "how much does
 * breaking this hurt" — this is a structural read of the graph *as loaded*,
 * not an LLM judgment, and is labelled that way in the panel copy. A node
 * this view hasn't loaded the edges for reads as "low" rather than
 * fabricating a worse number from nothing. */
function blastRadius(dependentCount: number): RiskLevel {
  if (dependentCount >= 8) return "critical";
  if (dependentCount >= 4) return "high";
  if (dependentCount >= 1) return "medium";
  return "low";
}

interface RelatedEntry {
  node: GraphNode;
  types: string[];
}

/** One direction's related-node list, deduped by node id with every edge
 * type between this node and that neighbor collected (a node can be
 * related through more than one edge type — e.g. both CALLS and IMPORTS). */
function relatedEntries(edges: GraphEdge[], neighborOf: (edge: GraphEdge) => string): Map<string, string[]> {
  const byId = new Map<string, string[]>();
  for (const edge of edges) {
    const id = neighborOf(edge);
    const types = byId.get(id) ?? [];
    if (!types.includes(edge.type)) types.push(edge.type);
    byId.set(id, types);
  }
  return byId;
}

/** ADR "Architecture Page V2" objective: node detail panels — now the
 * Architecture Intelligence redesign's "selected entity" pane. A side
 * panel (not a modal — the graph stays visible and interactive behind it)
 * for whatever node is currently selected in `DependencyGraph`.
 *
 * The previous version of this panel was a flat property dump (a `<dl>`
 * of every raw field the indexer wrote). That answers "what is the literal
 * database row" and nothing else — not "what does this affect", not "how
 * risky is touching it". This version computes real answers to those
 * questions from the graph edges already loaded (no fabricated content,
 * no LLM call this view doesn't make): who depends on this node, what it
 * depends on, and a blast-radius read derived from the first number. Raw
 * properties are still here, just demoted to a collapsed `<details>` at
 * the bottom instead of being the entire panel.
 *
 * Escape closes it — the same convention `CommandPalette` (this app's
 * other transient overlay) already establishes, not a new one invented
 * here. */
export function NodeDetailPanel({
  node,
  graph,
  onClose,
  onSelectNode,
  onExploreNeighbors,
  isExploringNeighbors,
  exploreLabel = "Explore neighbors",
  exploringLabel = "Loading neighbors…",
}: {
  node: GraphNode;
  /** The graph as currently loaded — used to compute this node's direct
   * dependencies/dependents. Edges to nodes outside what's loaded (a
   * paginated repository, or a neighborhood view's own boundary) aren't
   * visible here; the panel says so rather than under-counting silently. */
  graph: Graph;
  onClose: () => void;
  /** Jump the selection to a related node without leaving the panel —
   * turns "depends on" / "depended on by" from a static list into a way to
   * walk the graph. */
  onSelectNode: (nodeId: string) => void;
  /** Omitted entirely (not just a no-op) hides the button — the
   * Architecture lens's own "seed a fresh neighborhood from this node"
   * drill-down doesn't make sense everywhere `NodeDetailPanel` is reused
   * (e.g. Impact Check's blast radius already *is* a neighborhood; a
   * button that looks actionable but does nothing on click would be
   * worse than no button). */
  onExploreNeighbors?: () => void;
  isExploringNeighbors?: boolean;
  /** Same button, reused verbatim by the Dependency lens for "expand this
   * node's own dependencies" — only the label needs to differ ("Explore
   * neighbors" doesn't describe a directional expand). Defaults preserve
   * the Architecture lens's original wording exactly. */
  exploreLabel?: string;
  exploringLabel?: string;
}) {
  const [detailsOpen, setDetailsOpen] = useState(false);

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  // Collapse re-opens closed each time the selection changes — a stale
  // "Details" left expanded from the previous node reads as still
  // describing the new one until scrolled into view.
  useEffect(() => setDetailsOpen(false), [node.id]);

  const label = primaryLabel(node.labels);
  const colors = resolveLabelColors(label);
  const name = displayName(node);
  const properties = Object.entries(node.properties).filter(([key]) => !HIDDEN_PROPERTY_KEYS.has(key));

  const nodesById = useMemo(() => new Map(graph.nodes.map((n) => [n.id, n])), [graph.nodes]);

  const dependsOn = useMemo(
    () => relatedEntries(
      graph.edges.filter((e) => e.source_id === node.id),
      (e) => e.target_id,
    ),
    [graph.edges, node.id],
  );
  const dependedOnBy = useMemo(
    () => relatedEntries(
      graph.edges.filter((e) => e.target_id === node.id),
      (e) => e.source_id,
    ),
    [graph.edges, node.id],
  );

  const dependsOnEntries: RelatedEntry[] = [...dependsOn.entries()]
    .map(([id, types]) => ({ node: nodesById.get(id), types }))
    .filter((e): e is RelatedEntry => e.node !== undefined);
  const dependedOnByEntries: RelatedEntry[] = [...dependedOnBy.entries()]
    .map(([id, types]) => ({ node: nodesById.get(id), types }))
    .filter((e): e is RelatedEntry => e.node !== undefined);

  const risk = blastRadius(dependedOnByEntries.length);

  return (
    <aside
      aria-label="Node details"
      className="flex h-full w-full flex-col overflow-y-auto border-l border-line-muted bg-surface sm:w-[22rem]"
    >
      {/* ── Header ──────────────────────────────────────────────── */}
      <div className="flex items-start justify-between gap-2 border-b border-line-muted px-4 py-4">
        <div className="min-w-0">
          <span
            className="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium"
            style={{ background: colors.background, color: colors.text, border: `1px solid ${colors.border}` }}
          >
            {label}
          </span>
          <h3 className="mt-1.5 break-words font-display text-base font-semibold text-fg">{name}</h3>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close node details"
          className="focus-ring shrink-0 rounded-md p-1 text-fg-muted hover:bg-surface-raised hover:text-fg-secondary"
        >
          <X className="h-4 w-4" aria-hidden="true" />
        </button>
      </div>

      <div className="flex flex-col gap-5 px-4 py-4">
        {/* ── Impact — the "why does this matter" read ─────────── */}
        {/* ProvenanceTag "derived", not "AI insight": this is a
            deterministic count over loaded edges, not a model judgment —
            see ProvenanceTag's own docstring on why that distinction is
            drawn in the icon/color, not just a caption underneath. */}
        <section>
          <div className="mb-2 flex items-center justify-between gap-2">
            <p className="text-xs font-semibold tracking-wide text-fg-muted uppercase">Impact</p>
            <ProvenanceTag kind="derived" />
          </div>
          <div className="rounded-lg border border-line-muted bg-surface-raised p-3">
            <div className="flex items-center justify-between gap-2">
              <span className="text-xs font-medium text-fg-secondary">Blast radius</span>
              <RiskBadge level={risk} />
            </div>
            <p className="mt-2 text-xs leading-relaxed text-fg-muted">
              {dependedOnByEntries.length === 0 ? (
                <>Nothing currently loaded depends on this node directly — changes here are locally contained, as far as this view can see.</>
              ) : (
                <>
                  <span className="font-semibold text-fg-secondary">
                    {dependedOnByEntries.length} component{dependedOnByEntries.length === 1 ? "" : "s"}
                  </span>{" "}
                  depend{dependedOnByEntries.length === 1 ? "s" : ""} on this directly — a change here
                  can ripple into {dependedOnByEntries.length === 1 ? "it" : "all of them"}.
                </>
              )}
            </p>
          </div>
        </section>

        {onExploreNeighbors && (
          <button
            type="button"
            onClick={onExploreNeighbors}
            disabled={isExploringNeighbors}
            className="focus-ring inline-flex items-center justify-center gap-1.5 self-start rounded-md bg-accent-solid px-3 py-1.5 text-xs font-semibold text-accent-on-solid transition-colors hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-60"
          >
            <Compass className="h-3.5 w-3.5" aria-hidden="true" />
            {isExploringNeighbors ? exploringLabel : exploreLabel}
          </button>
        )}

        {/* ── Dependencies ───────────────────────────────────────── */}
        {(dependsOnEntries.length > 0 || dependedOnByEntries.length > 0) && (
          <section className="flex flex-col gap-4">
            <RelatedList
              icon={ArrowUpFromLine}
              title="Depends on"
              hint="what this needs to work"
              entries={dependsOnEntries}
              onSelectNode={onSelectNode}
            />
            <RelatedList
              icon={ArrowDownToLine}
              title="Depended on by"
              hint="what breaks if this changes"
              entries={dependedOnByEntries}
              onSelectNode={onSelectNode}
            />
          </section>
        )}

        {/* ── Raw properties — demoted, not deleted ─────────────── */}
        {properties.length > 0 && (
          <details
            open={detailsOpen}
            onToggle={(e) => setDetailsOpen(e.currentTarget.open)}
            className="rounded-lg border border-line-muted"
          >
            <summary className="focus-ring flex cursor-pointer list-none items-center justify-between gap-2 rounded-lg px-3 py-2 text-xs font-medium text-fg-muted hover:bg-surface-raised">
              <span className="flex items-center gap-1.5">
                <Search className="h-3.5 w-3.5" aria-hidden="true" />
                Raw properties ({properties.length})
              </span>
              <ChevronDown
                className={`h-3.5 w-3.5 shrink-0 transition-transform ${detailsOpen ? "rotate-180" : ""}`}
                aria-hidden="true"
              />
            </summary>
            <dl className="flex flex-col gap-2 border-t border-line-muted px-3 py-3 text-xs">
              {properties.map(([key, value]) => (
                <div key={key}>
                  <dt className="font-mono text-fg-muted">{key}</dt>
                  <dd className="break-words text-fg-secondary">
                    {typeof value === "object" ? JSON.stringify(value) : String(value)}
                  </dd>
                </div>
              ))}
            </dl>
          </details>
        )}
      </div>
    </aside>
  );
}

function RelatedList({
  icon: Icon,
  title,
  hint,
  entries,
  onSelectNode,
}: {
  icon: typeof ArrowUpFromLine;
  title: string;
  hint: string;
  entries: RelatedEntry[];
  onSelectNode: (nodeId: string) => void;
}) {
  if (entries.length === 0) return null;
  return (
    <div>
      <p className="flex items-baseline gap-1.5 text-xs font-semibold text-fg-secondary">
        <Icon className="h-3.5 w-3.5 shrink-0 text-fg-muted" aria-hidden="true" />
        {title}
        <span className="font-normal text-fg-subtle">· {hint}</span>
      </p>
      <ul className="mt-1.5 flex flex-col gap-1">
        {entries.map(({ node: related, types }) => {
          const relatedLabel = primaryLabel(related.labels);
          const colors = resolveLabelColors(relatedLabel);
          return (
            <li key={related.id}>
              <button
                type="button"
                onClick={() => onSelectNode(related.id)}
                className="focus-ring flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-xs transition-colors hover:bg-surface-raised"
              >
                <span
                  className="h-2 w-2 shrink-0 rounded-full"
                  style={{ background: colors.border }}
                  aria-hidden="true"
                />
                <span className="min-w-0 flex-1 truncate font-medium text-fg-secondary">
                  {displayName(related)}
                </span>
                <span className="shrink-0 truncate text-[10px] text-fg-subtle" title={types.join(", ")}>
                  {types[0]}
                  {types.length > 1 ? ` +${types.length - 1}` : ""}
                </span>
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
