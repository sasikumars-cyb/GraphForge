import {
  CheckCircle2,
  ChevronRight,
  FileQuestion,
  GitBranch,
  Layers,
  MapPin,
  ShieldAlert,
  Target,
} from "lucide-react";
import type { EngineeringUnderstandingDTO } from "../../types/agent";

// ---------------------------------------------------------------------------
// Shared tiny helpers — also used by AdvancedDetailsSection and DebugPanel,
// so the three disclosure levels read as one visual language rather than
// three differently-styled panels bolted together.
// ---------------------------------------------------------------------------

export function SectionHeading({
  icon: Icon,
  children,
}: {
  icon: typeof Target;
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

// ---------------------------------------------------------------------------
// Level 1 section components — the nine questions a reviewer asks first.
// Nothing here requires knowing how Context Discovery reached its
// conclusion; that belongs to Advanced Details (Level 2) or Debug (Level 3).
// ---------------------------------------------------------------------------

function BusinessGoal({ text }: { text: string }) {
  if (!text) return null;
  return (
    <section className="flex flex-col gap-1">
      <SectionHeading icon={Target}>Business Goal</SectionHeading>
      <Prose text={text} />
    </section>
  );
}

function CurrentSituation({ text }: { text: string }) {
  if (!text) return null;
  return (
    <section className="flex flex-col gap-1">
      <SectionHeading icon={MapPin}>Current Situation</SectionHeading>
      <Prose text={text} />
    </section>
  );
}

function ExpectedOutcome({ text }: { text: string }) {
  if (!text) return null;
  return (
    <section className="flex flex-col gap-1">
      <SectionHeading icon={CheckCircle2}>Expected Outcome</SectionHeading>
      <Prose text={text} />
    </section>
  );
}

function RepositorySummary({
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

function RelevantAreas({
  areas,
}: {
  areas: { name: string; components: string[] }[];
}) {
  if (areas.length === 0) return null;
  return (
    <section className="flex flex-col gap-1.5">
      <SectionHeading icon={Layers}>Relevant Areas</SectionHeading>
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

function KnownConstraints({ items }: { items: string[] }) {
  if (items.length === 0) return null;
  return (
    <section className="flex flex-col gap-1">
      <SectionHeading icon={ShieldAlert}>Known Constraints</SectionHeading>
      <BulletList items={items} />
    </section>
  );
}

function MissingInformation({ items }: { items: string[] }) {
  if (items.length === 0) return null;
  return (
    <section className="flex flex-col gap-1">
      <SectionHeading icon={FileQuestion}>Missing Information</SectionHeading>
      <BulletList items={items} />
    </section>
  );
}

function NextStep({ text }: { text: string }) {
  if (!text) return null;
  return (
    <section className="flex flex-col gap-1">
      <div className="rounded-lg border border-accent-line/30 bg-accent-bg px-3 py-2.5">
        <p className="flex items-center gap-1.5 text-xs font-medium text-accent-fg">
          <ChevronRight className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          {text}
        </p>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Main component — Level 1 of the progressive-disclosure hierarchy.
// ---------------------------------------------------------------------------

interface EngineeringUnderstandingPanelProps {
  dto: EngineeringUnderstandingDTO;
}

/** The default Context Explorer experience: "what did the system conclude?"
 * Answers exactly the questions a reviewer asks first — business goal,
 * current situation, expected outcome, repositories, architecture areas,
 * constraints, missing information, next step — and nothing else.
 * Everything about *how* the system got there (capability scores, evidence,
 * investigation trail) is progressively disclosed via
 * `AdvancedDetailsSection` and `DebugPanel`, not shown here.
 *
 * "Is Planning ready?" — the ninth Level 1 question — is deliberately not
 * repeated here: `ContextExplorerPanel` already renders one readiness/
 * confidence badge sourced from the workflow's own readiness field, and it
 * stays visible even if this DTO fails to load. Duplicating it here would
 * answer the same question twice from two different sources. */
export function EngineeringUnderstandingPanel({ dto }: EngineeringUnderstandingPanelProps) {
  return (
    <div className="flex flex-col gap-4">
      <BusinessGoal text={dto.business_goal} />
      <CurrentSituation text={dto.current_situation} />
      <ExpectedOutcome text={dto.expected_outcome} />
      <RepositorySummary
        primary={dto.repository_summary.primary}
        supporting={dto.repository_summary.supporting}
        ownership={dto.repository_summary.ownership}
      />
      <RelevantAreas areas={dto.relevant_areas} />
      <KnownConstraints items={dto.known_constraints} />
      <MissingInformation items={dto.missing_information} />
      <NextStep text={dto.next_step} />
    </div>
  );
}
