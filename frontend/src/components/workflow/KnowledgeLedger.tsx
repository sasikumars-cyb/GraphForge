import { Scale } from "lucide-react";
import type { EngineeringUnderstandingDTO } from "../../types/agent";
import { SectionHeading } from "./EngineeringUnderstandingPanel";

// ---------------------------------------------------------------------------
// Node 3 of the investigation story: "what does GraphForge currently
// believe, and how sure is it — not just one number?"
//
// Four buckets, re-labelled from real, already-typed states — nothing new
// invented:
//   Strongly supported <- hypotheses with status "supported", or (when no
//     hypothesis modeling ran at all) the grounded root-cause synthesis
//     text itself. Named "strongly supported," not "confirmed" — the
//     underlying text is evidence-grounded LLM synthesis, not a
//     deterministic proof, and the data contract makes no stronger
//     guarantee than that.
//   Inferred <- hypotheses with status "unknown" (proposed, not settled).
//   Contradicted <- hypotheses with status "rejected", plus open
//     (unresolved) contradictions.
//   Unknown <- `missing_information` + `unknowns` items already categorised
//     "unknown" by the backend.
//
// Under a degraded synthesis pass, Inferred/Contradicted are LLM-derived
// and therefore didn't run this cycle — they render an explicit
// "unavailable" placeholder rather than looking like a clean zero.
// Strongly-supported/Unknown are not LLM-hypothesis-dependent (they fall
// back to the deterministic evidence-only summary), so they still populate.
// ---------------------------------------------------------------------------

const MAX_VISIBLE_ITEMS = 4;

interface LedgerBucket {
  key: string;
  symbol: string;
  label: string;
  items: string[];
  tone: string;
  /** True only for the two buckets that come from LLM hypothesis/
   * contradiction synthesis and are therefore empty *because* synthesis
   * degraded, not because nothing was found. */
  unavailable?: boolean;
}

function LedgerColumn({ bucket }: { bucket: LedgerBucket }) {
  const shown = bucket.items.slice(0, MAX_VISIBLE_ITEMS);
  const hiddenCount = bucket.items.length - shown.length;

  return (
    <div className="flex min-h-[132px] flex-col gap-2 border-t border-line-muted px-3 py-3 first:border-t-0 sm:border-t-0 sm:border-l sm:first:border-l-0">
      <div className="flex items-center gap-1.5">
        <span className={`flex h-5 w-5 shrink-0 items-center justify-center rounded text-xs font-extrabold ${bucket.tone}`}>
          {bucket.symbol}
        </span>
        <span className="text-[11px] font-semibold text-fg-secondary">{bucket.label}</span>
        {!bucket.unavailable && (
          <span className="ml-auto font-mono text-[10px] text-fg-subtle">{bucket.items.length}</span>
        )}
      </div>
      {bucket.unavailable ? (
        <p className="rounded border border-dashed border-warning-line bg-warning-bg px-2 py-1.5 text-[10.5px] text-warning-fg italic">
          Not available — reasoning synthesis didn&apos;t complete this pass.
        </p>
      ) : shown.length > 0 ? (
        <ul className="flex flex-col gap-1">
          {shown.map((item) => (
            <li key={item} className="text-[11px] leading-snug text-fg-secondary">
              – {item}
            </li>
          ))}
          {hiddenCount > 0 && <li className="text-[10px] text-fg-subtle">and {hiddenCount} more</li>}
        </ul>
      ) : (
        <p className="text-[11px] text-fg-subtle italic">None found this pass.</p>
      )}
    </div>
  );
}

interface KnowledgeLedgerProps {
  understanding: EngineeringUnderstandingDTO;
}

export function KnowledgeLedger({ understanding }: KnowledgeLedgerProps) {
  const reasoning = understanding.reasoning_summary;
  const degraded = reasoning.degraded;

  const supportedHypotheses = reasoning.hypotheses
    .filter((h) => h.status === "supported")
    .map((h) => h.description);
  const stronglySupported =
    supportedHypotheses.length > 0
      ? supportedHypotheses
      : understanding.current_situation
        ? [understanding.current_situation]
        : [];

  const inferred = reasoning.hypotheses.filter((h) => h.status === "unknown").map((h) => h.description);

  const rejectedHypotheses = reasoning.hypotheses
    .filter((h) => h.status === "rejected")
    .map((h) => h.description);
  const openContradictions = reasoning.contradictions
    .filter((c) => !c.resolved)
    .map((c) => c.description);
  const contradicted = [...rejectedHypotheses, ...openContradictions];

  const unknown = [
    ...new Set([
      ...understanding.missing_information,
      ...understanding.unknowns.filter((u) => u.category === "unknown").map((u) => u.description),
    ]),
  ];

  const hasAnything =
    stronglySupported.length > 0 || inferred.length > 0 || contradicted.length > 0 || unknown.length > 0;
  if (!hasAnything) return null;

  const buckets: LedgerBucket[] = [
    {
      key: "supported",
      symbol: "✓",
      label: "Strongly supported",
      items: stronglySupported,
      tone: "bg-success-bg text-success-fg",
    },
    {
      key: "inferred",
      symbol: "~",
      label: "Inferred",
      items: inferred,
      tone: "bg-info-bg text-info-fg",
      unavailable: degraded,
    },
    {
      key: "contradicted",
      symbol: "⚠",
      label: "Contradicted",
      items: contradicted,
      tone: "bg-danger-bg text-danger-fg",
      unavailable: degraded,
    },
    {
      key: "unknown",
      symbol: "?",
      label: "Unknown",
      items: unknown,
      tone: "bg-neutral-bg text-neutral-fg",
    },
  ];

  return (
    <section className="flex flex-col gap-1.5">
      <SectionHeading icon={Scale}>What GraphForge believes — and how sure</SectionHeading>
      <p className="text-[11px] text-fg-subtle">
        Not just one confidence number: what&apos;s actually backed by evidence, inferred, contradicted,
        or still unknown.
      </p>
      <div className="grid grid-cols-1 divide-y divide-line-muted rounded-lg border border-line-muted bg-surface-raised sm:grid-cols-4 sm:divide-x sm:divide-y-0">
        {buckets.map((bucket) => (
          <LedgerColumn key={bucket.key} bucket={bucket} />
        ))}
      </div>
    </section>
  );
}
