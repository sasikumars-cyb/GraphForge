import type { ContradictionVM, ContradictionsSectionVM } from "../../lib/api/reports";
import { Card } from "../Card";
import { StatusBadge } from "../StatusBadge";
import { SynthesisStateNotice } from "./SynthesisStateNotice";

/** One contradiction, rendered as the five things a reader needs to act on
 * it: the contradiction itself, the evidence on each side, what it does to
 * the conclusion, and what would settle it. `impact` and
 * `required_resolution` are the backend's own derived text — this
 * component writes no verdict of its own. */
function ContradictionCard({ item }: { item: ContradictionVM }) {
  const { entry } = item;
  return (
    <div
      className={`rounded-lg border-2 p-4 ${
        item.is_blocking
          ? "border-danger-line/50 bg-danger-bg/30"
          : "border-warning-line/40 bg-warning-bg/40"
      }`}
    >
      <p className="text-sm leading-relaxed text-fg">{entry.statement}</p>
      <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div>
          <p className="mb-1 text-[11px] font-semibold text-success-fg">Supporting evidence</p>
          <ul className="flex flex-col gap-1">
            {entry.evidence_for.map((line, i) => (
              <li key={i} className="text-xs leading-relaxed text-fg-secondary">
                · {line}
              </li>
            ))}
          </ul>
        </div>
        <div>
          <p className="mb-1 text-[11px] font-semibold text-danger-fg">Conflicting evidence</p>
          <ul className="flex flex-col gap-1">
            {entry.evidence_against.map((line, i) => (
              <li key={i} className="text-xs leading-relaxed text-fg-secondary">
                · {line}
              </li>
            ))}
          </ul>
        </div>
      </div>
      <dl className="mt-3 flex flex-col gap-1.5 border-t border-line-muted pt-3 text-xs leading-relaxed">
        <div className="flex gap-2">
          <dt className="shrink-0 font-semibold text-fg-secondary">Impact on conclusion:</dt>
          <dd className="text-fg-secondary">{item.impact}</dd>
        </div>
        <div className="flex gap-2">
          <dt className="shrink-0 font-semibold text-fg-secondary">Required resolution:</dt>
          <dd className="text-fg-secondary">{item.required_resolution}</dd>
        </div>
      </dl>
      <div className="mt-3 flex items-center gap-2">
        <StatusBadge
          label={entry.resolved ? "Resolved" : "Unresolved — blocking"}
          tone={entry.resolved ? "success" : "danger"}
        />
        {entry.resolved && entry.resolution_note && (
          <span className="text-xs text-fg-muted">{entry.resolution_note}</span>
        )}
      </div>
    </div>
  );
}

/** [ Contradictions ] — deliberately more visually weighted than
 * Hypotheses (thicker, tinted border) so a reader's eye lands here — ADR
 * 0024 §9's "make contradictions visually prominent" instruction. */
export function ContradictionsSection({
  contradictions,
}: {
  contradictions: ContradictionsSectionVM;
}) {
  // COMPLETED and COMPLETED_EMPTY both mean "reasoning ran" — a
  // contradiction-free result reads the same positive way regardless of
  // whether hypotheses were also found. Only NOT_RUN/FAILED get the
  // shared cross-section notice (ADR 0024 §11).
  const ran =
    contradictions.synthesis_state === "completed" ||
    contradictions.synthesis_state === "completed_empty";

  if (!ran) {
    return (
      <Card title="Contradictions">
        <SynthesisStateNotice state={contradictions.synthesis_state} />
      </Card>
    );
  }

  if (contradictions.items.length === 0) {
    return (
      <Card title="Contradictions">
        <p className="text-xs text-fg-muted">
          No contradictions found — the evidence this investigation gathered never conflicted
          with itself.
        </p>
      </Card>
    );
  }

  const blocking = contradictions.items.filter((c) => c.is_blocking).length;

  return (
    <Card
      title="Contradictions"
      description={
        blocking > 0
          ? `${contradictions.items.length} found — ${blocking} unresolved and blocking`
          : `${contradictions.items.length} found, all resolved`
      }
    >
      <div className="flex flex-col gap-3">
        {contradictions.items.map((item, i) => (
          <ContradictionCard key={i} item={item} />
        ))}
      </div>
    </Card>
  );
}
