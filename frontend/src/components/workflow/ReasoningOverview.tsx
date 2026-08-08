import { AlertTriangle, CheckCircle2 } from "lucide-react";
import type { ContextDiscoveryResult, ContextReadiness, EngineeringUnderstandingDTO } from "../../types/agent";
import { CompletionStatusBadge } from "./EngineeringUnderstandingPanel";

// ---------------------------------------------------------------------------
// Node 1 of the "investigation story" Context Explorer now tells top to
// bottom: Question -> Investigated -> Believes -> Why -> Contradicts ->
// Unknown -> Next. This node answers the first two of those in one glance —
// what was being determined, and how sure the engine is right now — without
// the reader having to parse a bare percentage on its own (that's what the
// Knowledge Ledger below is for).
//
// Every value here already exists: `result.confidence`/`readiness` (always
// present once Context Discovery has run), `understanding.business_goal`/
// `current_situation`/`reasoning_summary.degraded` (present once the async
// /understanding fetch resolves — `understanding` is nullable so this node
// still renders, degraded gracefully, if that fetch is still loading or
// failed; see ContextExplorerPanel's existing resilience contract).
// ---------------------------------------------------------------------------

const READINESS_TONE: Record<ContextReadiness, { fg: string; bg: string; label: string }> = {
  READY: { fg: "text-success-fg", bg: "bg-success-bg", label: "Ready" },
  PARTIAL: { fg: "text-warning-fg", bg: "bg-warning-bg", label: "Partial" },
  BLOCKED: { fg: "text-danger-fg", bg: "bg-danger-bg", label: "Blocked" },
};

/** `confidence` is a real number only once Context Discovery has produced a
 * scored result — during `awaiting_input` it can be `undefined`/`NaN` (see
 * `ContextExplorerPanel.formatConfidence`'s own comment on the same issue).
 * The gauge must degrade the same honest way: an empty ring and a dash,
 * never a fabricated 0% or a NaN-derived arc. */
function ConfidenceGauge({
  confidence,
  readiness,
}: {
  confidence: number | null | undefined;
  readiness: ContextReadiness;
}) {
  const valid = typeof confidence === "number" && !Number.isNaN(confidence);
  const pct = valid ? Math.round(Math.max(0, Math.min(1, confidence)) * 100) : null;
  const tone = READINESS_TONE[readiness] ?? READINESS_TONE.BLOCKED;
  const radius = 26;
  const circumference = 2 * Math.PI * radius;
  const offset = pct === null ? circumference : circumference - (circumference * pct) / 100;

  const accessibleName = pct === null ? "Confidence not yet available" : `${pct}% confidence`;

  return (
    <div className="flex shrink-0 flex-col items-center gap-1" role="img" aria-label={accessibleName}>
      <div className="relative h-16 w-16">
        <svg width="64" height="64" viewBox="0 0 64 64" className="-rotate-90" aria-hidden="true">
          <circle cx="32" cy="32" r={radius} fill="none" strokeWidth="6" className="stroke-line-muted" />
          {pct !== null && (
            <circle
              cx="32"
              cy="32"
              r={radius}
              fill="none"
              strokeWidth="6"
              strokeLinecap="round"
              stroke="currentColor"
              className={tone.fg}
              strokeDasharray={circumference}
              strokeDashoffset={offset}
              style={{ transition: "stroke-dashoffset 0.4s ease" }}
            />
          )}
        </svg>
        <div
          className="absolute inset-0 flex items-center justify-center text-sm font-bold tabular-nums text-fg"
          aria-hidden="true"
        >
          {pct === null ? "–" : `${pct}%`}
        </div>
      </div>
      <span className={`text-[9px] font-bold uppercase tracking-wide ${tone.fg}`} aria-hidden="true">
        {tone.label}
      </span>
    </div>
  );
}

interface ReasoningOverviewProps {
  result: ContextDiscoveryResult;
  /** Nullable: the richer `/understanding` fetch may still be loading, or
   * may have failed while `result` itself (this component's other prop)
   * always exists — same resilience contract every other Context Explorer
   * section under `ContextExplorerPanel` already follows. */
  understanding: EngineeringUnderstandingDTO | null;
}

export function ReasoningOverview({ result, understanding }: ReasoningOverviewProps) {
  const question = understanding?.business_goal || result.original_request;
  const belief = understanding?.current_situation ?? "";
  const degraded = understanding?.reasoning_summary.degraded ?? false;

  const unknownCount = understanding
    ? new Set([
        ...understanding.missing_information,
        ...understanding.unknowns.filter((u) => u.category === "unknown").map((u) => u.description),
      ]).size
    : 0;

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-col gap-3 rounded-lg border border-line-muted bg-surface-raised px-3.5 py-3 sm:flex-row sm:items-center">
        <ConfidenceGauge confidence={result.confidence} readiness={result.readiness} />

        <div className="flex min-w-0 flex-1 flex-col gap-1.5">
          <p className="text-xs leading-snug text-fg-secondary">
            <span className="mr-1.5 text-[10px] font-semibold tracking-wide text-fg-subtle uppercase">
              Question
            </span>
            {question}
          </p>
          {belief && (
            <p className="text-xs leading-snug text-fg">
              <span className="mr-1.5 text-[10px] font-semibold tracking-wide text-fg-subtle uppercase">
                Currently believes
              </span>
              {belief}
            </p>
          )}
        </div>

        <div className="flex shrink-0 flex-col items-start gap-1.5 sm:items-end">
          <span
            className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-bold ${READINESS_TONE[result.readiness]?.bg ?? "bg-danger-bg"} ${READINESS_TONE[result.readiness]?.fg ?? "text-danger-fg"}`}
          >
            {result.readiness === "READY" ? (
              <CheckCircle2 className="h-3 w-3" aria-hidden="true" />
            ) : (
              <AlertTriangle className="h-3 w-3" aria-hidden="true" />
            )}
            {result.readiness}
          </span>
          <CompletionStatusBadge status={result.completion_status} />
          {understanding && unknownCount > 0 && (
            <span className="text-[10.5px] text-fg-subtle">
              {unknownCount} thing{unknownCount === 1 ? "" : "s"} still unknown ↓
            </span>
          )}
        </div>
      </div>

      {/* The single most important truthfulness requirement of this whole
          redesign: a failed synthesis pass must never look like "nothing to
          weigh." This banner is the one place that distinction is stated
          plainly, before a reader ever reaches the (thin-looking) Knowledge
          Ledger or hypothesis cards below. */}
      {degraded && (
        <div className="flex items-start gap-2 rounded-lg border border-warning-line/40 bg-warning-bg px-3 py-2.5">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-warning-fg" aria-hidden="true" />
          <p className="text-xs leading-snug font-medium text-warning-fg">
            Reasoning synthesis did not complete this pass.
            <span className="mt-0.5 block font-normal text-warning-fg/85">
              What&apos;s shown below is a deterministic, evidence-only summary — no fresh hypothesis
              or contradiction comparison ran. This is different from an investigation that
              genuinely found nothing to weigh.
            </span>
          </p>
        </div>
      )}
    </div>
  );
}
