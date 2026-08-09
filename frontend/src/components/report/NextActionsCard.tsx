import { ChevronRight } from "lucide-react";
import type { NextActionsSectionVM } from "../../lib/api/reports";
import { Card } from "../Card";

/** [ What's Next ] — the natural close of a 10-second read: one actionable
 * list, blocking items visually distinct from advisory ones. An empty list
 * is a real, positive outcome (nothing blocking) — shown as such, not as
 * an error or a dead end. */
export function NextActionsCard({ nextActions }: { nextActions: NextActionsSectionVM }) {
  if (nextActions.questions.length === 0) {
    return (
      <Card title="What's next">
        <p className="text-xs text-fg-muted">
          Nothing open or blocking was recorded — no follow-up is required before this can move
          forward.
        </p>
      </Card>
    );
  }

  return (
    <Card title="What's next">
      <ul className="flex flex-col divide-y divide-line-muted">
        {nextActions.questions.map((q, i) => (
          <li key={i} className="flex items-start gap-2 py-2 first:pt-0 last:pb-0">
            <span
              className={`mt-1 h-1.5 w-1.5 shrink-0 rounded-full ${
                q.is_blocking ? "bg-danger-solid" : "bg-warning-solid"
              }`}
              aria-hidden="true"
            />
            <p className="text-xs leading-relaxed text-fg-secondary">
              {q.is_blocking && (
                <span className="mr-1 font-semibold text-danger-fg">Blocking —</span>
              )}
              {q.text}
            </p>
          </li>
        ))}
      </ul>
      <p className="mt-3 flex items-center gap-1 text-[11px] text-fg-subtle">
        <ChevronRight className="h-3 w-3" aria-hidden="true" />
        {nextActions.questions.filter((q) => q.is_blocking).length} blocking,{" "}
        {nextActions.questions.filter((q) => !q.is_blocking).length} advisory
      </p>
    </Card>
  );
}
