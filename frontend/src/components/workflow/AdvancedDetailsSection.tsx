import { AlertTriangle, BookOpen, Check, FileText, Lightbulb, ListChecks, X } from "lucide-react";
import type { EngineeringUnderstandingDTO, PlanningFactorDTO } from "../../types/agent";
import { BulletList, Prose, SectionHeading } from "./EngineeringUnderstandingPanel";

// ---------------------------------------------------------------------------
// Level 2 — Advanced Details. Useful to an engineer deciding whether to trust
// the Level 1 verdict, without the raw execution internals that belong to
// Debug. No new fetch: every field here already arrives on the same
// (non-debug) EngineeringUnderstandingDTO response the default view uses.
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

interface AdvancedDetailsSectionProps {
  dto: EngineeringUnderstandingDTO;
}

/** Level 2 of the progressive-disclosure hierarchy: collapsed by default,
 * one click away. Answers "why should I trust the Level 1 verdict?" without
 * exposing raw execution internals — that's Debug's job. */
export function AdvancedDetailsSection({ dto }: AdvancedDetailsSectionProps) {
  return (
    <details className="group rounded-lg border border-line-muted bg-surface-raised px-3 py-2.5">
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
            <SectionHeading icon={FileText}>Architecture Summary</SectionHeading>
            <Prose text={dto.architecture_summary} />
          </section>
        )}
      </div>
    </details>
  );
}
