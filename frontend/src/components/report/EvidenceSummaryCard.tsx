import type { EvidenceSectionVM } from "../../lib/api/reports";
import { Card } from "../Card";
import { EmptyState } from "../EmptyState";

const KIND_LABEL: Record<string, string> = {
  graph_traversal: "Graph traversal",
  tool_call: "Tool call",
  graph_fact: "Graph fact",
  llm_reasoning: "LLM reasoning",
  human_input: "Human input",
};

/** [ Evidence & Provenance ] — bounded category counts (at most 5 kinds),
 * never a per-item list on this page — the full itemized trail already
 * lives in this workflow's own Context Explorer. A repository with 1,500
 * graph nodes still produces the same handful of tiles (ADR 0024 §12). */
export function EvidenceSummaryCard({ evidence }: { evidence: EvidenceSectionVM }) {
  if (evidence.availability.status === "unavailable" || evidence.categories.length === 0) {
    return (
      <Card title="Evidence & provenance">
        <EmptyState
          title="No evidence trail recorded"
          description={
            evidence.availability.reason ?? "Context Discovery recorded no evidence for this run."
          }
        />
      </Card>
    );
  }

  return (
    <Card title="Evidence & provenance" description={`${evidence.total} items, by category`}>
      <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-3">
        {evidence.categories.map((c) => (
          <div key={c.kind} className="rounded-lg bg-surface-raised px-3 py-2.5">
            <p className="font-display text-lg font-semibold tabular-nums text-fg">{c.count}</p>
            <p className="text-[11px] text-fg-muted">{KIND_LABEL[c.kind] ?? c.kind}</p>
          </div>
        ))}
      </div>
    </Card>
  );
}
