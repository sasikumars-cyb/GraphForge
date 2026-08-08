import { useState } from "react";
import type { ComponentType } from "react";
import {
  CheckCircle2,
  Compass,
  GitCompare,
  HelpCircle,
  History,
  Sparkles,
  XCircle,
} from "lucide-react";
import type { ContradictionDTO, HypothesisDTO, NextInvestigationDTO, ReasoningSummaryDTO } from "../../types/agent";
import { SectionHeading } from "./EngineeringUnderstandingPanel";

// ---------------------------------------------------------------------------
// The Reasoning view — GraphForge's own differentiator surfaced: the
// competing hypotheses the synthesis LLM weighed, the evidence for and
// against each, the conflicts it refused to silently average away, and
// what it decided was worth investigating next. All of this was already
// computed on every run; until this component existed it reached the
// frontend (see DebugBundleDTO.working_memory) and stopped there.
//
// Deliberately NOT a raw JSON dump and NOT a fabricated node-graph: a
// hypothesis's `supporting_evidence`/`contradicting_evidence` are the
// synthesis model's own prose citations, not evidence-ledger ids, so
// drawing literal graph edges between them and specific Evidence Details
// rows would assert a traceability this data doesn't actually have. What
// *is* real and structured — status, confidence, resolved/open, priority —
// gets a real visual encoding (badges, meters); what's prose stays prose,
// rendered as short, scannable bullets under an explicit "For"/"Against"
// split rather than a wall of paragraphs.
// ---------------------------------------------------------------------------

const STATUS_META: Record<
  HypothesisDTO["status"],
  { label: string; icon: ComponentType<{ className?: string; "aria-hidden"?: boolean | "true" | "false" }>; fg: string; bar: string }
> = {
  supported: { label: "Supported", icon: CheckCircle2, fg: "text-success-fg", bar: "bg-success-fg" },
  rejected: { label: "Rejected", icon: XCircle, fg: "text-danger-fg", bar: "bg-danger-fg" },
  unknown: { label: "Unknown", icon: HelpCircle, fg: "text-warning-fg", bar: "bg-warning-fg" },
};

/** A confidence/priority value rendered as a short filled track, never a
 * bare number — the same "never show a score without its shape" rule the
 * rest of Context Explorer already follows for capability confidence. */
function Meter({ value, barClassName = "bg-accent-solid" }: { value: number; barClassName?: string }) {
  const pct = Math.round(Math.max(0, Math.min(1, value)) * 100);
  return (
    <div className="flex shrink-0 items-center gap-1.5">
      <div className="h-1.5 w-14 overflow-hidden rounded-full bg-canvas ring-1 ring-inset ring-line-muted">
        <div className={`h-full rounded-full ${barClassName}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="w-8 shrink-0 text-right font-mono text-[10px] tabular-nums text-fg-subtle">{pct}%</span>
    </div>
  );
}

function HypothesisCard({ hypothesis }: { hypothesis: HypothesisDTO }) {
  const [expanded, setExpanded] = useState(hypothesis.is_strongest);
  const meta = STATUS_META[hypothesis.status];
  const Icon = meta.icon;
  const hasEvidence =
    hypothesis.supporting_evidence.length > 0 || hypothesis.contradicting_evidence.length > 0;

  return (
    <div
      className={`rounded-lg border px-3 py-2.5 ${
        hypothesis.is_strongest
          ? "border-accent-line/50 bg-accent-bg/40"
          : "border-line-muted bg-surface-raised"
      }`}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-start gap-1.5">
          <Icon className={`mt-0.5 h-3.5 w-3.5 shrink-0 ${meta.fg}`} aria-hidden="true" />
          <p className="text-xs leading-snug text-fg-secondary">{hypothesis.description}</p>
        </div>
        {hypothesis.is_strongest && (
          <span className="shrink-0 whitespace-nowrap rounded bg-accent-solid px-1.5 py-0.5 text-[10px] font-semibold text-accent-on-solid">
            Strongest
          </span>
        )}
      </div>
      <div className="mt-2 flex items-center justify-between gap-2 pl-5">
        <span className={`text-[10px] font-semibold uppercase tracking-wide ${meta.fg}`}>{meta.label}</span>
        <Meter value={hypothesis.confidence} barClassName={meta.bar} />
      </div>
      {hasEvidence && (
        <div className="pl-5">
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            className="focus-ring mt-1.5 rounded text-[10px] font-medium text-accent-fg hover:underline"
          >
            {expanded ? "Hide evidence" : "Show evidence"}
          </button>
          {expanded && (
            <div className="mt-1.5 flex flex-col gap-1.5">
              {hypothesis.supporting_evidence.length > 0 && (
                <div>
                  <p className="text-[10px] font-semibold text-success-fg">For</p>
                  <ul className="mt-0.5 flex flex-col gap-0.5">
                    {hypothesis.supporting_evidence.map((line) => (
                      <li key={line} className="text-[11px] text-fg-secondary">
                        + {line}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {hypothesis.contradicting_evidence.length > 0 && (
                <div>
                  <p className="text-[10px] font-semibold text-danger-fg">Against</p>
                  <ul className="mt-0.5 flex flex-col gap-0.5">
                    {hypothesis.contradicting_evidence.map((line) => (
                      <li key={line} className="text-[11px] text-fg-secondary">
                        − {line}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function ContradictionCard({ contradiction }: { contradiction: ContradictionDTO }) {
  return (
    <div className="rounded-lg border border-line-muted bg-surface-raised px-3 py-2.5">
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-start gap-1.5">
          <GitCompare className="mt-0.5 h-3.5 w-3.5 shrink-0 text-fg-muted" aria-hidden="true" />
          <p className="text-xs leading-snug text-fg-secondary">{contradiction.description}</p>
        </div>
        <span
          className={`shrink-0 whitespace-nowrap rounded px-1.5 py-0.5 text-[10px] font-semibold ${
            contradiction.resolved ? "bg-success-bg text-success-fg" : "bg-warning-bg text-warning-fg"
          }`}
        >
          {contradiction.resolved ? "Resolved" : "Open — being investigated"}
        </span>
      </div>
      <div className="mt-2 grid grid-cols-2 gap-2 pl-5">
        <div className="rounded-md bg-canvas px-2 py-1.5">
          <p className="text-[10px] font-semibold text-success-fg">Evidence for</p>
          {contradiction.evidence_for.length > 0 ? (
            <ul className="mt-0.5 flex flex-col gap-0.5">
              {contradiction.evidence_for.map((line) => (
                <li key={line} className="text-[11px] text-fg-secondary">
                  {line}
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-0.5 text-[11px] text-fg-subtle">—</p>
          )}
        </div>
        <div className="rounded-md bg-canvas px-2 py-1.5">
          <p className="text-[10px] font-semibold text-danger-fg">Evidence against</p>
          {contradiction.evidence_against.length > 0 ? (
            <ul className="mt-0.5 flex flex-col gap-0.5">
              {contradiction.evidence_against.map((line) => (
                <li key={line} className="text-[11px] text-fg-secondary">
                  {line}
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-0.5 text-[11px] text-fg-subtle">—</p>
          )}
        </div>
      </div>
      {contradiction.resolved && contradiction.resolution_note && (
        <p className="mt-1.5 pl-5 text-[11px] text-fg-muted">↳ {contradiction.resolution_note}</p>
      )}
    </div>
  );
}

function NextInvestigation({ items }: { items: NextInvestigationDTO[] }) {
  if (items.length === 0) return null;
  return (
    <section className="flex flex-col gap-1.5">
      <SectionHeading icon={Compass}>What GraphForge investigated next</SectionHeading>
      <p className="text-[11px] text-fg-subtle">
        Ranked by expected value at the last reasoning pass — highest first.
      </p>
      <div className="flex flex-col gap-1.5 rounded-lg border border-line-muted bg-surface-raised px-3 py-2.5">
        {items.map((item) => (
          <div key={item.capability} className="flex items-center gap-2">
            <span className="w-28 shrink-0 truncate text-[11px] text-fg-secondary">{item.label}</span>
            <Meter value={item.priority} />
          </div>
        ))}
      </div>
    </section>
  );
}

interface ReasoningSectionProps {
  summary: ReasoningSummaryDTO;
}

/** Collapsed by default, like Advanced Details and Debug — but its own
 * `<summary>` line always shows the headline count so its existence (and
 * whether anything needs attention) reads in the same 5-10 second glance
 * as the rest of Context Explorer, without forcing every viewer to expand
 * it. This is the section the audit found computed and thrown away before
 * reaching the UI at all. */
export function ReasoningSection({ summary }: ReasoningSectionProps) {
  const hasAnything =
    summary.has_reasoning || summary.next_investigation.length > 0 || summary.dead_ends.length > 0;
  const strongest = summary.hypotheses.find((h) => h.id === summary.strongest_hypothesis_id);

  return (
    <details className="group rounded-lg border border-line-muted bg-surface-raised px-3 py-2.5">
      <summary className="flex cursor-pointer flex-wrap items-center justify-between gap-2 text-xs font-semibold text-fg-secondary hover:text-fg">
        <span className="flex items-center gap-1.5">
          <Sparkles className="h-3.5 w-3.5 shrink-0 text-accent-fg" aria-hidden="true" />
          Investigation Reasoning
        </span>
        {hasAnything && (
          <span className="flex items-center gap-1.5 font-normal text-fg-subtle">
            {summary.hypotheses.length > 0 && (
              <span>
                {summary.hypotheses.length} hypothes{summary.hypotheses.length === 1 ? "is" : "es"}
              </span>
            )}
            {summary.contradictions.length > 0 && (
              <span
                className={
                  summary.open_contradiction_count > 0
                    ? "rounded bg-warning-bg px-1.5 py-0.5 text-warning-fg"
                    : ""
                }
              >
                {summary.contradictions.length} contradiction{summary.contradictions.length === 1 ? "" : "s"}
                {summary.open_contradiction_count > 0 ? ` (${summary.open_contradiction_count} open)` : " (resolved)"}
              </span>
            )}
          </span>
        )}
      </summary>

      <div className="mt-3 flex flex-col gap-4">
        {!hasAnything && (
          <p className="text-xs text-fg-subtle">
            No competing hypotheses or contradictions were needed for this investigation — the
            evidence gathered was straightforward enough not to require weighing alternatives.
          </p>
        )}

        {summary.degraded && (
          <p className="rounded-md bg-warning-bg px-2.5 py-1.5 text-[11px] text-warning-fg">
            Reasoning synthesis didn't complete cleanly on its last pass — what's shown below (if
            anything) is a deterministic, evidence-only summary rather than a full hypothesis
            comparison.
          </p>
        )}

        {strongest && (
          <p className="text-xs text-fg-secondary">
            <span className="font-medium text-fg">Strongest explanation: </span>
            {strongest.description}
          </p>
        )}

        {summary.hypotheses.length > 0 && (
          <section className="flex flex-col gap-1.5">
            <SectionHeading icon={Sparkles}>Hypotheses considered</SectionHeading>
            <div className="flex flex-col gap-2">
              {summary.hypotheses.map((h) => (
                <HypothesisCard key={h.id} hypothesis={h} />
              ))}
            </div>
          </section>
        )}

        {summary.contradictions.length > 0 && (
          <section className="flex flex-col gap-1.5">
            <SectionHeading icon={GitCompare}>Contradictions</SectionHeading>
            <p className="text-[11px] text-fg-subtle">
              Evidence that conflicted rather than agreed — never silently averaged away.
            </p>
            <div className="flex flex-col gap-2">
              {summary.contradictions.map((c) => (
                <ContradictionCard key={c.id} contradiction={c} />
              ))}
            </div>
          </section>
        )}

        <NextInvestigation items={summary.next_investigation} />

        {summary.dead_ends.length > 0 && (
          <details className="rounded-md bg-canvas px-2.5 py-1.5">
            <summary className="cursor-pointer text-[11px] font-medium text-fg-muted hover:text-fg-secondary">
              {summary.dead_ends.length} avenue{summary.dead_ends.length === 1 ? "" : "s"} ruled out
            </summary>
            <ul className="mt-1 flex flex-col gap-0.5 pl-3">
              {summary.dead_ends.map((line) => (
                <li key={line} className="text-[11px] text-fg-subtle">
                  {line}
                </li>
              ))}
            </ul>
          </details>
        )}

        {summary.last_update && (
          <p className="flex items-center gap-1 text-[10px] text-fg-subtle">
            <History className="h-3 w-3 shrink-0" aria-hidden="true" />
            {summary.last_update}
          </p>
        )}
      </div>
    </details>
  );
}
