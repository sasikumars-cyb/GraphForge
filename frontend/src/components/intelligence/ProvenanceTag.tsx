import { Database, GitBranch, Sparkles, UserCheck2, type LucideIcon } from "lucide-react";

/**
 * The GraphForge design system's single answer to "where did this claim
 * come from" — used everywhere the app shows something that isn't
 * obviously the user's own input: a fact retrieved verbatim from a source
 * system, a number GraphForge computed from graph structure, a claim an
 * LLM actually generated, or a call a human made.
 *
 * This exists because trust in an AI product lives or dies on the user
 * being able to tell these four apart at a glance, every time, in the same
 * visual language — not because any one instance needs decoration. Kept
 * deliberately restrained: one icon, one word, one tone per kind, and
 * `ai_insight` is the *only* one that uses the accent color. A page that
 * tags everything as AI-generated (including plain retrieved facts and
 * arithmetic) is the "purple gradient around everything labeled AI"
 * anti-pattern this was built to prevent.
 */
export type ProvenanceKind = "fact" | "derived" | "ai_insight" | "human_decision";

const PROVENANCE: Record<
  ProvenanceKind,
  { label: string; icon: LucideIcon; className: string }
> = {
  // Retrieved verbatim from GitHub/Jira/Confluence/the database — GraphForge
  // didn't compute or infer this, it just read it.
  fact: {
    label: "Source data",
    icon: Database,
    className: "bg-neutral-bg text-fg-secondary ring-line",
  },
  // A number or relationship GraphForge computed deterministically from
  // graph structure (edge counts, blast radius, aggregates) — not a model
  // judgment, reproducible from the same data every time.
  derived: {
    label: "Derived",
    icon: GitBranch,
    className: "bg-info-bg text-info-fg ring-info-line/40",
  },
  // An LLM actually generated this — a hypothesis, a recommendation, a
  // synthesized finding. The one kind that gets the accent treatment,
  // specifically so it stands out from the other three rather than
  // blending in with them.
  ai_insight: {
    label: "AI insight",
    icon: Sparkles,
    className: "bg-accent-bg text-accent-fg ring-accent-line/40",
  },
  // A person approved, rejected, or overrode something — the one kind
  // that's neither a machine fact nor a machine judgment.
  human_decision: {
    label: "Human decision",
    icon: UserCheck2,
    className: "bg-success-bg text-success-fg ring-success-line/40",
  },
};

export function ProvenanceTag({
  kind,
  label,
}: {
  kind: ProvenanceKind;
  /** Override the default word (e.g. "You corrected this") while keeping
   * the kind's icon/color — the taxonomy stays the same, only the copy
   * changes. */
  label?: string;
}) {
  const spec = PROVENANCE[kind];
  const Icon = spec.icon;
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium ring-1 ring-inset ${spec.className}`}
    >
      <Icon className="h-3 w-3 shrink-0" aria-hidden="true" />
      {label ?? spec.label}
    </span>
  );
}
