import { useEffect } from "react";
import { X } from "lucide-react";
import { StatusBadge } from "../StatusBadge";
import { primaryLabel, resolveLabelColors } from "../graph/graphLabels";
import type { GraphNode } from "../../types/graph";

const HIDDEN_PROPERTY_KEYS = new Set(["repository_id", "name"]);

/** ADR "Architecture Page V2" objective: node detail panels. A side panel
 * (not a modal — the graph stays visible and interactive behind it) for
 * whatever node is currently selected in `DependencyGraph`. `name` and
 * `repository_id` are surfaced as the panel's own title/subtitle rather
 * than repeated in the property list below.
 *
 * Escape closes it — the same convention `CommandPalette` (this app's
 * other transient overlay) already establishes, not a new one invented
 * here. */
export function NodeDetailPanel({
  node,
  onClose,
  onExploreNeighbors,
  isExploringNeighbors,
}: {
  node: GraphNode;
  onClose: () => void;
  /** Omitted entirely (not just a no-op) hides the button — the
   * Architecture lens's own "seed a fresh neighborhood from this node"
   * drill-down doesn't make sense everywhere `NodeDetailPanel` is reused
   * (e.g. Impact Check's blast radius already *is* a neighborhood; a
   * button that looks actionable but does nothing on click would be
   * worse than no button). */
  onExploreNeighbors?: () => void;
  isExploringNeighbors?: boolean;
}) {
  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  const label = primaryLabel(node.labels);
  const colors = resolveLabelColors(label);
  const name = String(node.properties.name ?? node.id);
  const properties = Object.entries(node.properties).filter(([key]) => !HIDDEN_PROPERTY_KEYS.has(key));

  return (
    <aside
      aria-label="Node details"
      className="flex h-full w-full flex-col gap-4 overflow-y-auto border-l border-line-muted bg-surface p-4 sm:w-80"
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <span
            className="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium"
            style={{ background: colors.background, color: colors.text, border: `1px solid ${colors.border}` }}
          >
            {label}
          </span>
          <h3 className="mt-1.5 break-words text-sm font-semibold text-fg">{name}</h3>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close node details"
          className="shrink-0 rounded-md p-1 text-fg-muted hover:bg-surface-raised hover:text-fg-secondary"
        >
          <X className="h-4 w-4" aria-hidden="true" />
        </button>
      </div>

      {node.labels.length > 1 && (
        <div className="flex flex-wrap gap-1">
          {node.labels
            .filter((l) => l !== "GraphNode")
            .map((l) => (
              <StatusBadge key={l} label={l} tone="neutral" />
            ))}
        </div>
      )}

      {onExploreNeighbors && (
        <button
          type="button"
          onClick={onExploreNeighbors}
          disabled={isExploringNeighbors}
          className="rounded-md bg-info-solid px-3 py-1.5 text-xs font-semibold text-black hover:brightness-110 disabled:cursor-not-allowed disabled:bg-info-bg"
        >
          {isExploringNeighbors ? "Loading neighbors…" : "Explore neighbors"}
        </button>
      )}

      {properties.length > 0 && (
        <div>
          <p className="mb-2 text-xs font-medium uppercase tracking-wide text-fg-muted">Properties</p>
          <dl className="flex flex-col gap-2 text-xs">
            {properties.map(([key, value]) => (
              <div key={key}>
                <dt className="font-mono text-fg-muted">{key}</dt>
                <dd className="break-words text-fg-secondary">
                  {typeof value === "object" ? JSON.stringify(value) : String(value)}
                </dd>
              </div>
            ))}
          </dl>
        </div>
      )}
    </aside>
  );
}
