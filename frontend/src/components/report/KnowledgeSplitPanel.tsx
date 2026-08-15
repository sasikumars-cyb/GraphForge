import { CircleCheck, HelpCircle } from "lucide-react";
import type { KnowledgeSectionVM } from "../../lib/api/reports";
import { Card } from "../Card";
import { EmptyState } from "../EmptyState";

/** [ Coverage & knowledge gaps ] — how much ground the investigation
 * covered, and what it never established.
 *
 * The left column is deliberately *not* titled "Known": it is a per-kind
 * count of everything the investigation recorded (verified or not), which
 * is a different claim from "this is proven". What is proven is listed,
 * individually, by `ConfirmedFindingsCard` above. Titling both "known"
 * made the document look like it carried the same section twice while
 * actually stating two different things. */
export function KnowledgeSplitPanel({ knowledge }: { knowledge: KnowledgeSectionVM }) {
  if (knowledge.availability.status === "unavailable") {
    return (
      <Card title="Coverage &amp; knowledge gaps">
        <EmptyState
          title="Nothing recorded yet"
          description={
            knowledge.availability.reason ?? "Context Discovery did not complete for this workflow."
          }
        />
      </Card>
    );
  }

  return (
    <Card title="Coverage &amp; knowledge gaps">
      <div className="grid grid-cols-1 gap-5 sm:grid-cols-2">
        <div>
          <p className="mb-2 flex items-center gap-1.5 text-xs font-semibold text-success-fg">
            <CircleCheck className="h-3.5 w-3.5" aria-hidden="true" />
            Recorded — how much ground was covered
          </p>
          <ul className="flex flex-col gap-1.5">
            {knowledge.known.map((line, i) => (
              <li key={i} className="text-xs leading-relaxed text-fg-secondary">
                {line}
              </li>
            ))}
          </ul>
          {knowledge.known.length === 0 && (
            <p className="text-xs italic text-fg-subtle">Nothing recorded.</p>
          )}
        </div>
        <div>
          <p className="mb-2 flex items-center gap-1.5 text-xs font-semibold text-fg-subtle">
            <HelpCircle className="h-3.5 w-3.5" aria-hidden="true" />
            Still unknown
          </p>
          <ul className="flex flex-col gap-1.5">
            {knowledge.unknown.map((line, i) => (
              <li key={i} className="text-xs leading-relaxed text-fg-secondary">
                {line}
              </li>
            ))}
          </ul>
          {knowledge.unknown.length === 0 && (
            <p className="text-xs italic text-fg-subtle">
              No open knowledge gaps were recorded — every capability the investigation touched
              reached a resolved state.
            </p>
          )}
        </div>
      </div>
    </Card>
  );
}
