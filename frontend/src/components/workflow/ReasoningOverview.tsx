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

// A one-sentence plain-language read of the confidence number — the
// number alone answers "how much," not "how much of what." Keyed on
// `readiness` only (never ticket content), so it's the same honest
// sentence for every investigation at a given readiness level.
const READINESS_EXPLANATION: Record<ContextReadiness, string> = {
  READY: "GraphForge gathered everything it needed and is confident in what it found.",
  PARTIAL:
    "GraphForge identified the likely repository and relevant source code, but some supporting information is still missing.",
  BLOCKED: "GraphForge could not gather enough information to proceed confidently.",
};

/** Context Discovery's number is evidence/context *completeness* — how much
 * of the configured signal set (work item / repository / architecture /
 * documentation / ...) it found — never confidence in an engineering
 * conclusion (see `ContextDiscoveryResult.context_completeness`'s own
 * docstring on the backend for the full distinction). Labeling this gauge
 * "confidence" is exactly the conflation that read as contradictory next to
 * a thin-looking Knowledge Ledger; "Context completeness" says what the
 * number actually is.
 *
 * The value itself is a real number only once Context Discovery has
 * produced a scored result — during `awaiting_input` it can be
 * `undefined`/`NaN` (see `ContextExplorerPanel.formatCompleteness`'s own
 * comment on the same issue). The gauge must degrade the same honest way:
 * an empty ring and a dash, never a fabricated 0% or a NaN-derived arc. */
function CompletenessGauge({
  completeness,
  readiness,
}: {
  completeness: number | null | undefined;
  readiness: ContextReadiness;
}) {
  const valid = typeof completeness === "number" && !Number.isNaN(completeness);
  const pct = valid ? Math.round(Math.max(0, Math.min(1, completeness)) * 100) : null;
  const tone = READINESS_TONE[readiness] ?? READINESS_TONE.BLOCKED;
  const radius = 26;
  const circumference = 2 * Math.PI * radius;
  const offset = pct === null ? circumference : circumference - (circumference * pct) / 100;

  const accessibleName =
    pct === null ? "Context completeness not yet available" : `${pct}% context completeness`;

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
      <span
        className="text-[9px] font-bold uppercase tracking-wide text-fg-subtle"
        aria-hidden="true"
      >
        Context completeness
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
        <CompletenessGauge
          completeness={result.context_completeness ?? result.confidence}
          readiness={result.readiness}
        />

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

      {/* A plain-language read of the number above it — a non-technical
          reader shouldn't have to infer what "83% · Partial" means on
          their own. */}
      <p className="text-xs leading-relaxed text-fg-secondary">
        {READINESS_EXPLANATION[result.readiness] ?? READINESS_EXPLANATION.BLOCKED}
      </p>

      {/* Distinguishes *what* didn't finish from *whether the
          investigation itself succeeded* — the single most important
          truthfulness requirement of this whole page. Fact-gathering
          (Context Discovery) and root-cause reasoning (Engineering
          Analysis) are two different steps; when only the second one
          didn't finish, showing that plainly (not as "reasoning synthesis
          degraded to a deterministic summary") is what keeps a reader from
          concluding GraphForge found nothing when it actually found
          real, useful evidence. */}
      {degraded && (
        <div className="flex flex-col gap-2 rounded-lg border border-warning-line/40 bg-warning-bg px-3 py-2.5">
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs font-medium">
            <span className="flex items-center gap-1.5 text-success-fg">
              <CheckCircle2 className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
              Context Discovery — Completed
            </span>
            <span className="flex items-center gap-1.5 text-warning-fg">
              <AlertTriangle className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
              Engineering Analysis — Not completed
            </span>
          </div>
          <p className="text-xs leading-snug text-warning-fg/85">
            GraphForge finished gathering evidence, but couldn&apos;t complete the deeper
            root-cause analysis for this run. What&apos;s shown below reflects the evidence it
            gathered, not a conclusion it reasoned through — different from an investigation
            that looked and genuinely found nothing.
          </p>
        </div>
      )}
    </div>
  );
}
