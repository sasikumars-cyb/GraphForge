import { ArrowRight, HelpCircle } from "lucide-react";
import type { NextInvestigationDTO, UnknownItemDTO } from "../../types/agent";
import { Meter, SectionHeading } from "./EngineeringUnderstandingPanel";

// ---------------------------------------------------------------------------
// Node 6, the close of the investigation story: "what's still unknown, and
// what happens next?" Promoted out of Advanced Details — unknowns used to
// only surface as a bare count ("2 things still unclear — see Advanced
// Details"); this renders the actual items, always visible, never buried
// behind a click a viewer has to know to make.
//
// `next_investigation` used to live inside `ReasoningSection` (see that
// component's own history) — moved here so "what's next" isn't shown twice
// and closes the narrative loop the Investigation Timeline's own dashed
// "next" row opens.
// ---------------------------------------------------------------------------

interface UnknownsAndNextProps {
  missingInformation: string[];
  unknowns: UnknownItemDTO[];
  nextInvestigation: NextInvestigationDTO[];
}

export function UnknownsAndNext({ missingInformation, unknowns, nextInvestigation }: UnknownsAndNextProps) {
  const unknownDescriptions = [
    ...new Set([...missingInformation, ...unknowns.filter((u) => u.category === "unknown").map((u) => u.description)]),
  ];
  const next = nextInvestigation[0] ?? null;

  if (unknownDescriptions.length === 0 && !next) return null;

  return (
    <section className="flex flex-col gap-2.5 rounded-lg border border-line-muted bg-surface-raised px-3 py-2.5">
      {unknownDescriptions.length > 0 && (
        <div className="flex flex-col gap-1.5">
          <SectionHeading icon={HelpCircle}>What GraphForge still doesn&apos;t know</SectionHeading>
          <div className="flex flex-wrap gap-1.5">
            {unknownDescriptions.map((item) => (
              <span
                key={item}
                className="rounded-md bg-neutral-bg px-2 py-1 text-[11px] text-neutral-fg ring-1 ring-inset ring-neutral-line"
              >
                {item}
              </span>
            ))}
          </div>
        </div>
      )}

      {next && (
        <div className="flex items-start gap-2 rounded-md bg-accent-bg px-2.5 py-2">
          <ArrowRight className="mt-0.5 h-3.5 w-3.5 shrink-0 text-accent-fg" aria-hidden="true" />
          <div className="min-w-0 flex-1">
            <p className="text-[10px] font-semibold tracking-wide text-accent-fg uppercase">
              Investigating next
            </p>
            <p className="text-xs text-fg-secondary">{next.label}</p>
            <div className="mt-1 flex items-center gap-1.5">
              <Meter value={next.priority} />
              <span className="text-[10px] text-fg-subtle">expected value, highest first</span>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
