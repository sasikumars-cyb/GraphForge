import type { ContradictionEntry, ContradictionsSectionVM } from "../../lib/api/reports";
import { Card } from "../Card";
import { StatusBadge } from "../StatusBadge";
import { SynthesisStateNotice } from "./SynthesisStateNotice";

function ContradictionCard({ item }: { item: ContradictionEntry }) {
  return (
    <div className="rounded-lg border-2 border-warning-line/40 bg-warning-bg/40 p-4">
      <p className="text-sm leading-relaxed text-fg">{item.statement}</p>
      <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div>
          <p className="mb-1 text-[11px] font-semibold text-success-fg">Supporting evidence</p>
          <ul className="flex flex-col gap-1">
            {item.evidence_for.map((line, i) => (
              <li key={i} className="text-xs leading-relaxed text-fg-secondary">
                · {line}
              </li>
            ))}
          </ul>
        </div>
        <div>
          <p className="mb-1 text-[11px] font-semibold text-danger-fg">Contradicting evidence</p>
          <ul className="flex flex-col gap-1">
            {item.evidence_against.map((line, i) => (
              <li key={i} className="text-xs leading-relaxed text-fg-secondary">
                · {line}
              </li>
            ))}
          </ul>
        </div>
      </div>
      <div className="mt-3 flex items-center gap-2">
        <StatusBadge
          label={item.resolved ? "Resolved" : "Unresolved"}
          tone={item.resolved ? "success" : "warning"}
        />
        {item.resolved && item.resolution_note && (
          <span className="text-xs text-fg-muted">{item.resolution_note}</span>
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

  return (
    <Card title="Contradictions" description={`${contradictions.items.length} found`}>
      <div className="flex flex-col gap-3">
        {contradictions.items.map((item, i) => (
          <ContradictionCard key={i} item={item} />
        ))}
      </div>
    </Card>
  );
}
