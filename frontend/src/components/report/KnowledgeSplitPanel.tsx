import { CircleCheck, HelpCircle } from "lucide-react";
import type { KnowledgeSectionVM } from "../../lib/api/reports";
import { Card } from "../Card";
import { EmptyState } from "../EmptyState";

/** [ What We Know / Don't Know ] — placed before Hypotheses (ADR 0024 §3):
 * a reader needs "what's known" as a frame before "what's being debated"
 * makes sense. Two columns, same card, so the contrast between known and
 * unknown reads at a glance rather than requiring a scroll between them. */
export function KnowledgeSplitPanel({ knowledge }: { knowledge: KnowledgeSectionVM }) {
  if (knowledge.availability.status === "unavailable") {
    return (
      <Card title="What we know / don't know">
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
    <Card title="What we know / don't know">
      <div className="grid grid-cols-1 gap-5 sm:grid-cols-2">
        <div>
          <p className="mb-2 flex items-center gap-1.5 text-xs font-semibold text-success-fg">
            <CircleCheck className="h-3.5 w-3.5" aria-hidden="true" />
            Known
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
            Unknown
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
