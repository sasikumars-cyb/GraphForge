import { Clock, FileQuestion, Layers, Search } from "lucide-react";
import type React from "react";
import type { CompletionStatus } from "../../types/agent";

// ---------------------------------------------------------------------------
// Shared building blocks for every disclosure level of Context Explorer
// (`InvestigationSummary` for the first screen, `AdvancedDetailsSection` and
// `DebugPanel` for the two levels beneath it) — kept in one file so all
// three read as one visual language rather than three differently-styled
// panels bolted together.
//
// The full narrative view this file used to export (`EngineeringUnderstand
// ingPanel`, showing Business Goal/Current Situation/Expected Outcome/
// Repository/Relevant Areas/Known Constraints/Missing Information/Next Step
// as one flat, always-visible list) has been superseded by
// `InvestigationSummary` — the same facts, curated into an engineering
// investigation brief instead of a flat field dump. `RelevantAreas` and
// `MissingInformation` below are the two pieces of that old view with
// genuinely more detail than the first screen shows (the full per-area
// component list; the full missing-information list, not just a count) —
// they now live in Advanced Details instead.
// ---------------------------------------------------------------------------

export function SectionHeading({
  icon: Icon,
  children,
}: {
  icon: React.ComponentType<{ className?: string; "aria-hidden"?: boolean | "true" | "false" }>;
  children: React.ReactNode;
}) {
  return (
    <h3 className="flex items-center gap-1.5 text-xs font-semibold text-fg-secondary">
      <Icon className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
      {children}
    </h3>
  );
}

export function Prose({ text }: { text: string }) {
  if (!text) return null;
  return <p className="text-xs leading-relaxed text-fg-secondary">{text}</p>;
}

export function BulletList({ items }: { items: string[] }) {
  if (items.length === 0) return null;
  return (
    <ul className="flex flex-col gap-0.5">
      {items.map((item) => (
        <li key={item} className="text-xs text-fg-secondary">
          · {item}
        </li>
      ))}
    </ul>
  );
}

/** The full per-area component breakdown — `InvestigationSummary` shows
 * only each area's name as a chip; this is the detail behind that chip. */
export function RelevantAreas({ areas }: { areas: { name: string; components: string[] }[] }) {
  if (areas.length === 0) return null;
  return (
    <section className="flex flex-col gap-1.5">
      <SectionHeading icon={Layers}>Relevant Areas</SectionHeading>
      <p className="text-[11px] text-fg-subtle">
        What's involved, grouped by area — see Architecture Relationships for how they connect.
      </p>
      {areas.map((area) => (
        <div key={area.name} className="rounded-lg bg-surface-raised px-3 py-2">
          <p className="text-xs font-medium text-fg-secondary">{area.name}</p>
          {area.components.length > 0 && (
            <p className="mt-0.5 text-xs text-fg-muted">{area.components.join(", ")}</p>
          )}
        </div>
      ))}
    </section>
  );
}

/** The full missing-information list — `InvestigationSummary` shows only a
 * count ("N things still unclear"); this is what they are. */
export function MissingInformation({ items }: { items: string[] }) {
  if (items.length === 0) return null;
  return (
    <section className="flex flex-col gap-1">
      <SectionHeading icon={FileQuestion}>Missing Information</SectionHeading>
      <BulletList items={items} />
    </section>
  );
}

/** Only rendered for the two `completion_status` values that say something
 * `readiness` alone doesn't: BUDGET_EXHAUSTED and PROVIDERS_EXHAUSTED. For
 * COMPLETED/PARTIAL/BLOCKED, the existing readiness badge already reads as
 * exactly that word, so a second identical-looking chip beside it would be
 * decoration, not information — the audit's own "don't add visuals that
 * don't answer a real question faster than text" rule applied to itself. */
export function CompletionStatusBadge({ status }: { status: CompletionStatus }) {
  if (status === "BUDGET_EXHAUSTED") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-warning-bg px-2 py-0.5 text-[10px] font-semibold text-warning-fg">
        <Clock className="h-3 w-3 shrink-0" aria-hidden="true" />
        Stopped at cycle limit
      </span>
    );
  }
  if (status === "PROVIDERS_EXHAUSTED") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-accent-bg px-2 py-0.5 text-[10px] font-semibold text-accent-fg">
        <Search className="h-3 w-3 shrink-0" aria-hidden="true" />
        Every automated avenue tried
      </span>
    );
  }
  return null;
}
