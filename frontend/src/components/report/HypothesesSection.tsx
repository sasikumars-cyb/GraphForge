import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import type { HypothesisVM, HypothesesSectionVM } from "../../lib/api/reports";
import { Card } from "../Card";
import { SynthesisStatusBadge, VerificationStatusBadge } from "./badges";
import { SynthesisStateNotice } from "./SynthesisStateNotice";

function ConfidenceBar({ confidence }: { confidence: number }) {
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-surface-raised">
        <div
          className="h-full rounded-full bg-info-fg"
          style={{ width: `${Math.max(confidence * 100, 2)}%` }}
        />
      </div>
      <span className="w-9 shrink-0 text-right text-[11px] tabular-nums text-fg-muted">
        {Math.round(confidence * 100)}%
      </span>
    </div>
  );
}

function HypothesisCard({ item }: { item: HypothesisVM }) {
  const [expanded, setExpanded] = useState(false);
  const { entry } = item;
  const hasEvidence = entry.supporting_evidence.length > 0 || entry.contradicting_evidence.length > 0;

  return (
    <div className="flex flex-col gap-2.5 rounded-lg border border-line-muted bg-surface p-4">
      <p className="text-sm leading-relaxed text-fg">{entry.statement}</p>
      <div className="flex flex-wrap gap-1.5">
        <SynthesisStatusBadge status={entry.status} />
        <VerificationStatusBadge status={item.verification_status} />
      </div>
      <ConfidenceBar confidence={entry.confidence} />
      {hasEvidence && (
        <div>
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            className="flex items-center gap-3 text-[11px] text-fg-muted hover:text-fg-secondary"
            aria-expanded={expanded}
          >
            {expanded ? (
              <ChevronDown className="h-3 w-3" aria-hidden="true" />
            ) : (
              <ChevronRight className="h-3 w-3" aria-hidden="true" />
            )}
            Supporting ({entry.supporting_evidence.length}) · Contradicting (
            {entry.contradicting_evidence.length})
          </button>
          {expanded && (
            <div className="mt-2 rounded-md bg-surface-raised px-3 py-2 text-[11px] leading-relaxed text-fg-muted">
              {entry.supporting_evidence.length > 0 && (
                <p className="mb-1">
                  <span className="font-semibold text-fg-secondary">Supporting: </span>
                  {entry.supporting_evidence.join(" · ")}
                </p>
              )}
              {entry.contradicting_evidence.length > 0 && (
                <p>
                  <span className="font-semibold text-fg-secondary">Contradicting: </span>
                  {entry.contradicting_evidence.join(" · ")}
                </p>
              )}
              <p className="mt-1.5 italic text-fg-subtle">
                The reasoning engine's own notes, in prose — not linked evidence records. See
                Evidence &amp; provenance below for what was actually verified.
              </p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/** [ Potential root cause / hypotheses ] — competing explanations,
 * strongest first (the backend already sorts by confidence descending —
 * position itself answers "why does it believe the strongest
 * hypothesis"). ADR 0024 §8.
 *
 * The title says "potential" and the subheading says "unconfirmed" on
 * purpose: nothing in this section is established. What *is* established
 * is rendered above, by `ConfirmedFindingsCard`, and a hypothesis only
 * moves there by being verified — never by having high confidence. */
export function HypothesesSection({ hypotheses }: { hypotheses: HypothesesSectionVM }) {
  if (hypotheses.synthesis_state !== "completed" || hypotheses.items.length === 0) {
    return (
      <Card title="Potential root cause / hypotheses">
        <SynthesisStateNotice state={hypotheses.synthesis_state} />
      </Card>
    );
  }

  return (
    <Card
      title="Potential root cause / hypotheses"
      description={`${hypotheses.items.length + hypotheses.truncated_count} considered, strongest first — none of these is a confirmed root cause`}
    >
      <p className="mb-3 text-xs leading-relaxed text-fg-muted">
        These are candidate explanations, not conclusions. The percentage on each card is
        confidence in that one hypothesis — not confidence that the issue is understood; see
        Confidence &amp; readiness for that.
      </p>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {hypotheses.items.map((item, i) => (
          <HypothesisCard key={i} item={item} />
        ))}
      </div>
      {hypotheses.truncated_count > 0 && (
        <p className="mt-3 text-[11px] text-fg-subtle">
          + {hypotheses.truncated_count} more, lower confidence — kept out of this view for scale.
        </p>
      )}
    </Card>
  );
}
