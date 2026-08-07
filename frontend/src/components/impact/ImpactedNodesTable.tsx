import { primaryLabel } from "../graph/graphLabels";
import type { GraphNode } from "../../types/graph";

/** Supporting detail for `BlastRadiusGraph` — a sortable table beneath the
 * visualization, synced to the same selection state (per ARCHITECTURE_
 * EXPERIENCE_REDESIGN.md's own rule: text supports the visual, shown
 * alongside/beneath it, not instead of it). Selecting a row highlights
 * the corresponding graph node and vice versa. */
export function ImpactedNodesTable({
  nodes,
  seedNodeId,
  selectedNodeId,
  onSelect,
}: {
  nodes: GraphNode[];
  seedNodeId: string;
  selectedNodeId: string | null;
  onSelect: (node: GraphNode) => void;
}) {
  const sorted = [...nodes]
    .filter((n) => n.id !== seedNodeId)
    .sort((a, b) => {
      const hopA = typeof a.properties.hop_distance === "number" ? a.properties.hop_distance : 0;
      const hopB = typeof b.properties.hop_distance === "number" ? b.properties.hop_distance : 0;
      if (hopA !== hopB) return hopA - hopB;
      return String(a.properties.name ?? a.id).localeCompare(String(b.properties.name ?? b.id));
    });

  if (sorted.length === 0) {
    return <p className="py-4 text-center text-sm text-fg-muted">No impact beyond the seed node.</p>;
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <thead>
          <tr className="border-b border-line-muted text-xs text-fg-muted">
            <th scope="col" className="py-2 pr-3 font-medium">
              Component
            </th>
            <th scope="col" className="py-2 pr-3 font-medium">
              Type
            </th>
            <th scope="col" className="py-2 pr-3 font-medium">
              Hops away
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-line-muted">
          {sorted.map((node) => {
            const hop = typeof node.properties.hop_distance === "number" ? node.properties.hop_distance : 0;
            const isSelected = node.id === selectedNodeId;
            return (
              <tr
                key={node.id}
                onClick={() => onSelect(node)}
                aria-selected={isSelected}
                className={`cursor-pointer ${isSelected ? "bg-info-bg" : "hover:bg-surface-raised"}`}
              >
                <td className="py-2 pr-3 text-fg-secondary">
                  {String(node.properties.name ?? node.id)}
                </td>
                <td className="py-2 pr-3 text-fg-muted">{primaryLabel(node.labels)}</td>
                <td className="py-2 pr-3 text-fg-muted">{hop}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
