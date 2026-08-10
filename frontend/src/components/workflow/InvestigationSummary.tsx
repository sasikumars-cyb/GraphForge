import {
  CheckCircle2,
  ChevronRight,
  FileCode,
  FileQuestion,
  Lightbulb,
  ShieldCheck,
} from "lucide-react";
import type { ContextDiscoveryResult, EngineeringUnderstandingDTO } from "../../types/agent";
import { SectionHeading } from "./EngineeringUnderstandingPanel";

// ---------------------------------------------------------------------------
// The first screen of Context Explorer — what an engineer opening this
// ticket needs to understand within 20-30 seconds, without expanding
// Advanced Details or Debug. Every value here already exists on
// `ContextDiscoveryResult`/`EngineeringUnderstandingDTO` (both already
// fetched by `ContextExplorerPanel`); this component only curates and
// orders it, it introduces nothing new.
//
// Layout (in order):
//   1. Files to Review      — ranked production file paths (understanding.files_to_review)
//   2. Proposed Change      — recommendations (what should change)
//   3. Acceptance Criteria  — expected_outcome (what "done" means)
//   4. Why GraphForge believes this — confidence + evidence grounding
//
// The business goal ("Question") and root cause ("Currently believes") that
// used to open this list now live in `ReasoningOverview` — node 1 of the
// investigation-story Context Explorer composes above this component (see
// `ContextExplorerPanel`). Showing `understanding.business_goal`/
// `current_situation` a second time here would be the exact "text-heavy
// dashboard" the Reasoning Visualization redesign was meant to avoid — same
// two sentences, twice, in two different boxes.
//
// Supporting information (repository, areas, constraints, missing info)
// lives in Advanced Details. Implementation internals stay in Debug.
// ---------------------------------------------------------------------------

interface InvestigationSummaryProps {
  result: ContextDiscoveryResult;
  understanding: EngineeringUnderstandingDTO;
}

/** Level 1, screen 1 (continued): the engineering investigation brief —
 * everything an engineer needs beyond the story already told above. */
export function InvestigationSummary({ result, understanding }: InvestigationSummaryProps) {
  // `understanding.files_to_review` is the curated, ranked must-modify /
  // architecture-dependency selection from the Evidence Package (see
  // engineering_understanding_mapper._map_files_to_review) — production
  // files preferred, capped, never a raw dump. Deliberately no fallback to
  // `result.graph_components` here: that's the complete, unranked component
  // list (hundreds of entries on a real repo, dominated by test names
  // simply because there are more of them), and reading it directly when
  // curation came back empty is exactly the regression this component once
  // shipped — "Files to Review" silently became a wall of test files while
  // the one production file the investigation had just named as the root
  // cause wasn't even in it. An empty `files_to_review` is a real signal
  // (no file cleared curation's bar) and must render as the honest empty
  // state below, not be silently replaced with something less trustworthy.
  const files = understanding.files_to_review;
  const evidenceCount = result.discovery_report?.findings?.reduce(
    (total, group) => total + group.total,
    0,
  );

  return (
    <div className="flex flex-col gap-3">
      {/* 1 — Files to Review: the concrete file paths an engineer should
          open first — the curated evidence package's own must_modify
          tier (ranked, deduped), not the raw component list. */}
      <div className="rounded-lg border border-line-muted bg-surface-raised px-3 py-2.5">
        <SectionHeading icon={FileCode}>Files to Review</SectionHeading>
        {files.length > 0 ? (
          <ul className="mt-1 flex flex-col gap-0.5">
            {files.map((path) => (
              <li key={path} className="truncate font-mono text-[11px] text-fg-secondary">
                {path}
              </li>
            ))}
          </ul>
        ) : (
          <div className="mt-1 flex flex-col gap-0.5">
            <p className="text-xs text-fg-subtle">No clearly relevant files identified.</p>
            <p className="text-[11px] text-fg-subtle">
              The investigation did not identify a production file with sufficient evidence to
              recommend — see Advanced Details for the full component list.
            </p>
          </div>
        )}
      </div>

      {/* 2 — Proposed Change: what should change — the actionable
          recommendations from Context Discovery's analysis. */}
      <div className="rounded-lg border border-line-muted bg-surface-raised px-3 py-2.5">
        <SectionHeading icon={Lightbulb}>Proposed Change</SectionHeading>
        {understanding.recommendations.length > 0 ? (
          <ul className="mt-1 flex flex-col gap-0.5">
            {understanding.recommendations.map((item) => (
              <li key={item} className="text-xs text-fg-secondary">
                · {item}
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-1 text-xs text-fg-subtle">No specific recommendation yet.</p>
        )}
      </div>

      {/* 3 — Acceptance Criteria: what "done" means — sourced from the
          same field the ticket's own AC section fills when one exists. */}
      {understanding.expected_outcome && (
        <div className="rounded-lg border border-line-muted bg-surface-raised px-3 py-2.5">
          <SectionHeading icon={CheckCircle2}>Acceptance Criteria</SectionHeading>
          <p className="mt-1 text-xs leading-relaxed text-fg-secondary">
            {understanding.expected_outcome}
          </p>
        </div>
      )}

      {/* 4 — Why GraphForge believes this: the trust footer — confidence
          explanation plus how much evidence it's grounded in. Promoted here
          because "why should I trust this" is a first-20-seconds question. */}
      {(understanding.confidence_explanation || evidenceCount) && (
        <div className="rounded-lg border border-line-muted bg-canvas px-3 py-2.5">
          <SectionHeading icon={ShieldCheck}>Why GraphForge believes this</SectionHeading>
          {understanding.confidence_explanation && (
            <p className="mt-1 text-xs leading-relaxed text-fg-secondary">
              {understanding.confidence_explanation}
            </p>
          )}
          {typeof evidenceCount === "number" && evidenceCount > 0 && (
            <p className="mt-1 text-[11px] text-fg-subtle">
              Grounded in {evidenceCount} recorded fact{evidenceCount === 1 ? "" : "s"} — see
              Advanced Details for the full evidence trail.
            </p>
          )}
          {understanding.missing_information.length > 0 && (
            <p className="mt-1 flex items-center gap-1 text-[11px] text-warning-fg">
              <FileQuestion className="h-3 w-3 shrink-0" aria-hidden="true" />
              {understanding.missing_information.length} thing
              {understanding.missing_information.length === 1 ? "" : "s"} still unclear — see
              Advanced Details.
            </p>
          )}
        </div>
      )}

      {/* Next step — the one actionable CTA, the natural close of a
          20-second read. */}
      {understanding.next_step && (
        <div className="rounded-lg border border-accent-line/30 bg-accent-bg px-3 py-2.5">
          <p className="flex items-center gap-1.5 text-xs font-medium text-accent-fg">
            <ChevronRight className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
            {understanding.next_step}
          </p>
        </div>
      )}
    </div>
  );
}
