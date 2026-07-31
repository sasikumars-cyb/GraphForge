import { useState } from "react";
import {
  AlertTriangle,
  BookOpen,
  Check,
  Database,
  FileText,
  Lightbulb,
  ListChecks,
  Loader2,
  Waypoints,
  X,
} from "lucide-react";
import type {
  CapabilityBreakdown,
  DebugBundleDTO,
  EngineeringUnderstandingDTO,
  FindingGroup,
  PlanningFactorDTO,
} from "../../types/agent";
import { BulletList, Prose, SectionHeading } from "./EngineeringUnderstandingPanel";

// ---------------------------------------------------------------------------
// Level 2 — Advanced Details. Useful to an engineer deciding whether to trust
// the Level 1 verdict, without the implementation-only internals that stay
// in Debug (raw reasoning, graph traversal, retrieval data, transcripts,
// raw payloads). Most fields here arrive on the same (non-debug)
// EngineeringUnderstandingDTO response the default view uses; Capability
// Signals and Evidence Details are the exception — they live on
// `debug_bundle`, so opening this section for the first time makes the same
// `?debug=true` request Debug does (no new backend contract, just reusing
// the existing endpoint's existing parameter).
// ---------------------------------------------------------------------------

function CapabilityReadiness({ reasons }: { reasons: PlanningFactorDTO[] }) {
  if (reasons.length === 0) return null;
  return (
    <section className="flex flex-col gap-1.5">
      <SectionHeading icon={ListChecks}>Capability Readiness</SectionHeading>
      <ul className="flex flex-col gap-1 rounded-lg border border-line-muted bg-surface-raised px-3 py-2.5">
        {reasons.map((r) => (
          <li key={r.description} className="flex items-start gap-1.5 text-xs">
            {r.satisfied ? (
              <Check className="mt-0.5 h-3 w-3 shrink-0 text-success-fg" aria-hidden="true" />
            ) : (
              <X className="mt-0.5 h-3 w-3 shrink-0 text-fg-muted" aria-hidden="true" />
            )}
            <span className={r.satisfied ? "text-fg-secondary" : "text-fg-muted"}>
              {r.description}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function ConfidenceExplanation({ text }: { text: string }) {
  if (!text) return null;
  return (
    <section className="flex flex-col gap-1">
      <SectionHeading icon={ListChecks}>Confidence Explanation</SectionHeading>
      <Prose text={text} />
    </section>
  );
}

function Unknowns({ items }: { items: { category: string; description: string }[] }) {
  if (items.length === 0) return null;
  const BADGE: Record<string, string> = {
    known: "bg-accent-bg text-accent-fg",
    unknown: "bg-warning-bg text-warning-fg",
    unavailable: "bg-surface-raised text-fg-muted",
  };
  return (
    <section className="flex flex-col gap-1">
      <SectionHeading icon={AlertTriangle}>Unknowns (detailed)</SectionHeading>
      <ul className="flex flex-col gap-1">
        {items.map((item) => (
          <li key={item.description} className="flex items-start gap-2 text-xs text-fg-secondary">
            <span
              className={`mt-0.5 shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium leading-tight ${BADGE[item.category] ?? BADGE.unknown}`}
            >
              {item.category}
            </span>
            {item.description}
          </li>
        ))}
      </ul>
    </section>
  );
}

function EvidenceSummary({ items }: { items: string[] }) {
  if (items.length === 0) return null;
  return (
    <section className="flex flex-col gap-1">
      <SectionHeading icon={BookOpen}>Evidence Summary</SectionHeading>
      <BulletList items={items} />
    </section>
  );
}

function Recommendations({ items }: { items: string[] }) {
  if (items.length === 0) return null;
  return (
    <section className="flex flex-col gap-1">
      <SectionHeading icon={Lightbulb}>Recommendations</SectionHeading>
      <BulletList items={items} />
    </section>
  );
}

function DocumentationStatus({ text }: { text: string }) {
  if (!text) return null;
  return (
    <section className="flex flex-col gap-1">
      <SectionHeading icon={FileText}>Documentation Status</SectionHeading>
      <Prose text={text} />
    </section>
  );
}

/** Per-capability confidence with the signals that produced it — a score is
 * never shown without the decomposition it was computed from. Routine
 * trust-building content, not a debugging tool, which is why it lives here
 * rather than in Debug. */
function CapabilitySignals({ items }: { items: CapabilityBreakdown[] }) {
  const applicable = items.filter((item) => item.necessity !== "not_applicable");
  if (applicable.length === 0) return null;

  return (
    <section className="flex flex-col gap-1.5">
      <SectionHeading icon={ListChecks}>Capability Signals</SectionHeading>
      <div className="flex flex-col gap-2.5">
        {applicable.map((item) => (
          <div key={item.capability} className="rounded-lg bg-surface-raised px-3 py-2">
            <div className="flex items-baseline justify-between gap-2">
              <p className="text-xs font-medium text-fg-secondary">
                {item.label}
                {item.necessity === "recommended" && (
                  <span className="ml-1.5 text-fg-subtle">(optional)</span>
                )}
              </p>
              <span
                className={`text-sm font-semibold ${
                  item.satisfied ? "text-success-fg" : "text-warning-fg"
                }`}
              >
                {Math.round(item.score * 100)}%
              </span>
            </div>
            <ul className="mt-1.5 flex flex-col gap-1">
              {item.signals.map((signal) => (
                <li key={signal.label} className="flex items-start gap-1.5 text-xs">
                  {signal.satisfied ? (
                    <Check
                      className="mt-0.5 h-3 w-3 shrink-0 text-success-fg"
                      aria-hidden="true"
                    />
                  ) : (
                    <X className="mt-0.5 h-3 w-3 shrink-0 text-fg-muted" aria-hidden="true" />
                  )}
                  <span className={signal.satisfied ? "text-fg-secondary" : "text-fg-muted"}>
                    {signal.label}
                    {!signal.satisfied && signal.detail && (
                      <span className="text-fg-subtle"> — {signal.detail}</span>
                    )}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </section>
  );
}

/** Facts grouped by kind, each with the evidence that established it. An
 * unverified item is a human claim investigation could not corroborate, and
 * is labelled as such rather than displayed as knowledge. Distinct from
 * "Evidence Summary" above: that's the curated, tiered summary every
 * engineer sees; this is the granular, per-finding breakdown behind it. */
function EvidenceDetails({ groups }: { groups: FindingGroup[] }) {
  const visible = groups.filter((g) => g.items.length > 0);
  if (visible.length === 0) return null;

  return (
    <section className="flex flex-col gap-1.5">
      <SectionHeading icon={Database}>Evidence Details</SectionHeading>
      <div className="flex flex-col gap-2">
        {visible.map((group) => (
          <div key={group.kind} className="rounded-lg bg-surface-raised px-3 py-2">
            <p className="text-xs font-medium text-fg-secondary capitalize">
              {group.kind.replace(/_/g, " ")}
              {group.total > group.items.length && (
                <span className="ml-1.5 font-normal text-fg-subtle">
                  showing {group.items.length} of {group.total}
                </span>
              )}
            </p>
            <ul className="mt-1 flex flex-col gap-1">
              {group.items.map((item) => (
                <li key={item.fact_id} className="text-xs">
                  <span className="text-fg-secondary">{item.subject}</span>
                  {!item.verified && (
                    <span className="ml-1.5 rounded bg-warning-bg px-1 py-0.5 text-warning-fg">
                      unverified claim
                    </span>
                  )}
                  {item.evidence && (
                    <span className="block text-fg-subtle">↳ {item.evidence.summary}</span>
                  )}
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </section>
  );
}

interface AdvancedDetailsSectionProps {
  dto: EngineeringUnderstandingDTO;
  /** The same `debug_bundle` Debug reads — Capability Signals and Evidence
   * Details are the two fields from it shown here. `null` until fetched. */
  bundle: DebugBundleDTO | null;
  isLoading: boolean;
  error: string | null;
  /** Called once, the first time an engineer expands this section. Shared
   * with Debug's `onExpand` — whichever section opens first triggers the
   * one `?debug=true` request both read from. */
  onExpand: () => void;
}

/** Level 2 of the progressive-disclosure hierarchy: collapsed by default,
 * one click away. Answers "why should I trust the Level 1 verdict?" —
 * including capability signals and evidence details, which are routine
 * trust-building content an engineer reaches for often, not implementation
 * internals reserved for Debug. */
export function AdvancedDetailsSection({
  dto,
  bundle,
  isLoading,
  error,
  onExpand,
}: AdvancedDetailsSectionProps) {
  const [hasExpanded, setHasExpanded] = useState(false);

  return (
    <details
      className="group rounded-lg border border-line-muted bg-surface-raised px-3 py-2.5"
      onToggle={(e) => {
        if (e.currentTarget.open && !hasExpanded) {
          setHasExpanded(true);
          onExpand();
        }
      }}
    >
      <summary className="cursor-pointer text-xs font-semibold text-fg-secondary hover:text-fg">
        Advanced Details
      </summary>
      <div className="mt-3 flex flex-col gap-4">
        <ConfidenceExplanation text={dto.confidence_explanation} />
        <CapabilityReadiness reasons={dto.planning_assessment.reasons} />
        <Unknowns items={dto.unknowns} />
        <EvidenceSummary items={dto.evidence_summary} />
        <Recommendations items={dto.recommendations} />
        <DocumentationStatus text={dto.documentation_status} />
        {dto.architecture_summary && (
          <section className="flex flex-col gap-1">
            <SectionHeading icon={Waypoints}>Architecture Relationships</SectionHeading>
            <p className="text-[11px] text-fg-subtle">
              How the areas above connect to each other — not a restatement of them.
            </p>
            <Prose text={dto.architecture_summary} />
          </section>
        )}
        {isLoading && (
          <div className="flex items-center gap-2 text-xs text-fg-muted">
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
            Loading capability signals and evidence details…
          </div>
        )}
        {error && <p className="text-xs text-danger-fg">{error}</p>}
        {bundle && (
          <>
            <CapabilitySignals
              items={bundle.confidence_breakdown as unknown as CapabilityBreakdown[]}
            />
            <EvidenceDetails groups={bundle.findings as unknown as FindingGroup[]} />
          </>
        )}
      </div>
    </details>
  );
}
