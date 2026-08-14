import { useState } from "react";
import {
  AlertTriangle,
  BookOpen,
  Check,
  Database,
  FileText,
  GitBranch,
  ListChecks,
  Loader2,
  ShieldAlert,
  Waypoints,
  X,
} from "lucide-react";
import type {
  CapabilityBreakdown,
  ContextDiscoveryResult,
  DebugBundleDTO,
  EngineeringUnderstandingDTO,
  FindingGroup,
  PlanningFactorDTO,
} from "../../types/agent";
import {
  BulletList,
  MissingInformation,
  Prose,
  RelevantAreas,
  SectionHeading,
} from "./EngineeringUnderstandingPanel";
import { InvestigationTimeline } from "./InvestigationTimeline";
import { KnowledgeLedger } from "./KnowledgeLedger";
import { ReasoningSection } from "./ReasoningSection";

// ---------------------------------------------------------------------------
// Level 2 — Advanced Details. The detail behind the first screen
// (`InvestigationSummary`) for an engineer deciding whether to trust its
// verdict — the full per-area component breakdown and missing-information
// list the first screen only shows a name/count for, plus everything the
// first screen doesn't surface at all (capability readiness, unknowns,
// evidence summary, documentation status, architecture relationships).
// Confidence Explanation and Recommendations moved to the first screen
// itself and are deliberately not repeated here. Implementation-only
// internals (raw reasoning, graph traversal, retrieval data, transcripts,
// raw payloads) stay in Debug. Most fields here arrive on the same
// (non-debug) EngineeringUnderstandingDTO response the default view uses;
// Capability
// Signals and Evidence Details are the exception — they live on
// `debug_bundle`, so opening this section for the first time makes the same
// `?debug=true` request Debug does (no new backend contract, just reusing
// the existing endpoint's existing parameter).
//
// UX review (GraphForge Frontier redesign): `InvestigationTimeline`
// ("What GraphForge investigated" — the raw, per-cycle tool-call log),
// `KnowledgeLedger` (the "Strongly supported / Inferred / Contradicted /
// Unknown" evidence-classification grid) and `ReasoningSection` (the
// hypothesis/contradiction breakdown) used to render always-open in the
// primary Context Explorer flow — a non-technical reader hit 25 raw
// "graph · survey_architecture" log lines, and a 4-bucket grid reading
// "Strongly supported: 0" sitting directly beside an 83% confidence
// number, before ever reaching the plain-language verdict. All three are
// real, valuable engineering detail — just not the first thing "did
// GraphForge find what I need?" requires. They now render inside this
// already-collapsed section instead, reusing the exact same components
// (no logic changes), so opening "Technical Details" is what surfaces them.
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

function DocumentationStatus({ text }: { text: string }) {
  if (!text) return null;
  return (
    <section className="flex flex-col gap-1">
      <SectionHeading icon={FileText}>Documentation Status</SectionHeading>
      <Prose text={text} />
    </section>
  );
}

function KnownConstraints({ items }: { items: string[] }) {
  if (items.length === 0) return null;
  return (
    <section className="flex flex-col gap-1">
      <SectionHeading icon={ShieldAlert}>Known Constraints</SectionHeading>
      <BulletList items={items} />
    </section>
  );
}

/** A small uppercase divider between the five groups this section is
 * organized into (Evidence / Investigation / Graph & Architecture /
 * Reasoning) — tells an engineer why they'd open each part instead of
 * presenting one continuous, unlabeled scroll of sections. */
function GroupLabel({ children }: { children: string }) {
  return (
    <p className="border-t border-line-muted pt-3 text-[10px] font-bold tracking-wider text-fg-subtle uppercase first:border-t-0 first:pt-0">
      {children}
    </p>
  );
}

function RepositoryInfo({
  primary,
  supporting,
  ownership,
}: {
  primary: string;
  supporting: string[];
  ownership: string[];
}) {
  if (!primary && supporting.length === 0) return null;
  return (
    <section className="flex flex-col gap-1">
      <SectionHeading icon={GitBranch}>Repository</SectionHeading>
      {primary && (
        <p className="text-xs text-fg-secondary">
          <span className="font-medium">Primary:</span> {primary}
        </p>
      )}
      {supporting.length > 0 && (
        <p className="text-xs text-fg-muted">
          <span className="font-medium text-fg-secondary">Supporting:</span>{" "}
          {supporting.join(", ")}
        </p>
      )}
      {ownership.length > 0 && (
        <p className="text-xs text-fg-muted">
          <span className="font-medium text-fg-secondary">Owners:</span> {ownership.join(", ")}
        </p>
      )}
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
  /** Only `discovery_report.investigation` is read from this — the raw,
   * per-cycle investigation log `InvestigationTimeline` renders. Everything
   * else this section shows comes from `dto`. */
  result: ContextDiscoveryResult;
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
  result,
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
        Technical Details
        <span className="mt-0.5 block cursor-auto text-[11px] font-normal text-fg-subtle">
          Source evidence, graph relationships, investigation steps, and reasoning — for engineers
          verifying or debugging this result.
        </span>
      </summary>
      <div className="mt-3 flex flex-col gap-4">
        {/* 1. Evidence — what backs the confidence number, and how much of
            it there is. */}
        <GroupLabel>Evidence</GroupLabel>
        <EvidenceSummary items={dto.evidence_summary} />
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

        {/* 2. Investigation steps — the raw, per-cycle action log. Real
            and bounded (see InvestigationTimeline's own docs), but
            implementation detail: "graph · survey_architecture" isn't
            what a reader deciding whether to trust the verdict needs
            first. */}
        <GroupLabel>Investigation steps</GroupLabel>
        <InvestigationTimeline
          steps={result.discovery_report?.investigation ?? []}
          nextInvestigation={dto.reasoning_summary.next_investigation}
        />
        <MissingInformation items={dto.missing_information} />
        <Unknowns items={dto.unknowns} />
        <DocumentationStatus text={dto.documentation_status} />

        {/* 3. Graph & architecture — what the investigation is scoped to
            and how the pieces connect. */}
        <GroupLabel>Graph &amp; architecture</GroupLabel>
        <RepositoryInfo
          primary={dto.repository_summary.primary}
          supporting={dto.repository_summary.supporting}
          ownership={dto.repository_summary.ownership}
        />
        <RelevantAreas areas={dto.relevant_areas} />
        {dto.architecture_summary && (
          <section className="flex flex-col gap-1">
            <SectionHeading icon={Waypoints}>Architecture Relationships</SectionHeading>
            <p className="text-[11px] text-fg-subtle">
              How the areas above connect to each other — not a restatement of them.
            </p>
            <Prose text={dto.architecture_summary} />
          </section>
        )}

        {/* 4. Reasoning — the capability-readiness detail behind "What we
            know," the evidence-classification ledger, and the
            hypothesis/contradiction breakdown. Moved out of the primary
            flow: "Strongly supported: 0" sitting directly beside an 83%
            confidence number read as contradictory, when what it actually
            meant was "no LLM hypothesis synthesis ran this pass" — a
            distinction this section's own copy already makes correctly,
            it just needed not to compete with the headline verdict for
            attention. */}
        <GroupLabel>Reasoning</GroupLabel>
        <CapabilityReadiness reasons={dto.planning_assessment.reasons} />
        <KnownConstraints items={dto.known_constraints} />
        <KnowledgeLedger understanding={dto} />
        <ReasoningSection summary={dto.reasoning_summary} />
      </div>
    </details>
  );
}
