import {
  Check,
  ChevronRight,
  Circle,
  FileCode,
  Lightbulb,
  ShieldCheck,
  TriangleAlert,
} from "lucide-react";
import type { ContextDiscoveryResult, EngineeringUnderstandingDTO } from "../../types/agent";
import { SectionHeading } from "./EngineeringUnderstandingPanel";

// ---------------------------------------------------------------------------
// The first screen of Context Explorer — what an engineer (or a
// non-technical reader) opening this ticket needs to understand within
// 20-30 seconds, without expanding Advanced Details or Debug. Every value
// here already exists on `ContextDiscoveryResult`/`EngineeringUnderstandingDTO`
// (both already fetched by `ContextExplorerPanel`); this component only
// curates and orders it, it introduces nothing new.
//
// Layout (in order):
//   1. Files to Review      — ranked production file paths, split Primary/
//                              Related (understanding.files_to_review)
//   2. What's supported     — the capability checklist behind the
//                              confidence number, in plain language
//   3. Still uncertain      — genuine content gaps, kept separate from
//                              system/investigation limitations (a failed
//                              LLM synthesis pass is not "GraphForge found
//                              nothing" and must never read as one)
//   4. Proposed Change      — recommendations (what should change)
//   5. Acceptance Criteria  — expected_outcome (what "done" means)
//   6. Why GraphForge believes this — confidence + evidence grounding
//   7. Next step            — a real, working control, not styled text
//
// The business goal ("Question") and root cause ("Currently believes") that
// used to open this list live in `ReasoningOverview` — node 1 of the
// investigation-story Context Explorer composes above this component (see
// `ContextExplorerPanel`). Showing `understanding.business_goal`/
// `current_situation` a second time here would be the exact "text-heavy
// dashboard" the Reasoning Visualization redesign was meant to avoid — same
// two sentences, twice, in two different boxes.
//
// Supporting information (repository, areas, constraints, the raw
// investigation log, the hypothesis/contradiction breakdown) lives in
// Advanced Details. Implementation internals stay in Debug.
// ---------------------------------------------------------------------------

interface InvestigationSummaryProps {
  result: ContextDiscoveryResult;
  understanding: EngineeringUnderstandingDTO;
}

const MAX_PRIMARY_FILES = 2;

// Fixed, backend-authored system messages (see
// `app.context_pipeline.reasoning.understanding._describe_synthesis_failure`
// on the backend) — never ticket content, always this exact wording
// whenever engineering synthesis degraded or failed to run. Matching on
// this literal prefix is what lets "still uncertain" separate a genuine
// content gap ("no design documentation was found") from a system
// limitation ("the reasoning pass failed") without the backend having to
// tag them differently.
const SYSTEM_LIMITATION_PREFIX = "Engineering synthesis (";

function isSystemLimitation(description: string): boolean {
  return description.startsWith(SYSTEM_LIMITATION_PREFIX);
}

// The same two fixed, backend-authored system messages `isSystemLimitation`
// matches on (never ticket content) — translated into plain language here.
// "Engineering synthesis" (the LLM hypothesis/root-cause reasoning pass) is
// internal terminology; a reader doesn't need to know GraphForge has an
// internal step called that to understand "this part of the analysis
// didn't finish." Falls back to the original backend text for any future
// system message this dictionary doesn't yet know about, rather than
// hiding it.
const SYSTEM_LIMITATION_TRANSLATIONS: Record<string, string> = {
  "Engineering synthesis (hypothesis reasoning, cross-source insight) did not run for this investigation — the fields above are a deterministic summary of the curated evidence only.":
    "Engineering analysis (root-cause reasoning) did not run for this investigation — what's shown is based on the evidence gathered only.",
  "Engineering synthesis (LLM reasoning pass) failed — see logs for the underlying error.":
    "Engineering analysis could not be completed for this run — see Technical Details for the underlying error.",
};

function translateLimitation(description: string): string {
  return SYSTEM_LIMITATION_TRANSLATIONS[description] ?? description;
}

// The fixed, small vocabulary of capability labels the backend actually
// produces (see `app.context_pipeline.reasoning.capabilities` — "Work
// item", "Repository", "Architecture", "Documentation", "Runtime
// Execution"). Translated into the "✓ thing identified" phrasing a
// non-technical reader expects instead of a bare capability name; any
// label this dictionary doesn't recognize (a future capability) falls
// back to that raw name rather than showing nothing.
const CAPABILITY_COPY: Record<string, { satisfied: string; outstanding: string }> = {
  "Work item": { satisfied: "Jira issue identified", outstanding: "Jira issue not yet identified" },
  Repository: { satisfied: "Repository identified", outstanding: "Repository not yet identified" },
  Architecture: {
    satisfied: "Relevant source code found",
    outstanding: "Relevant source code not yet found",
  },
  Documentation: {
    satisfied: "Design documentation found",
    outstanding: "No design documentation found",
  },
  "Runtime Execution": {
    satisfied: "Runtime behavior verified",
    outstanding: "Runtime behavior not yet verified",
  },
};

function capabilityCopy(label: string, satisfied: boolean): string {
  const entry = CAPABILITY_COPY[label];
  if (!entry) return label;
  return satisfied ? entry.satisfied : entry.outstanding;
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
  const primaryFiles = files.slice(0, MAX_PRIMARY_FILES);
  const relatedFiles = files.slice(MAX_PRIMARY_FILES);

  // The number of real evidence items behind the verdict — the same count
  // the Evidence Trail below shows ("N verifiable items"), sourced from
  // the same `investigation` log it's built from. Deliberately not a sum
  // of `findings[].total` (the raw recorded-fact count, which can run into
  // the thousands on a large repository and is exactly the internal-
  // metrics jargon this screen exists to not show) — "12,213 recorded
  // facts" doesn't tell a reader anything a smaller, real "25 evidence
  // items" doesn't say better.
  const evidenceCount = result.discovery_report?.investigation?.length;

  // Same dedup the Knowledge Ledger's "Unknown" bucket uses — genuine
  // content gaps and system-level investigation limitations arrive
  // together on `missing_information`/`unknowns`; this is the one place
  // that separates them so a failed reasoning pass never gets presented
  // as "here's everything that's actually missing about your ticket."
  const allUncertain = [
    ...new Set([
      ...understanding.missing_information,
      ...understanding.unknowns.filter((u) => u.category === "unknown").map((u) => u.description),
    ]),
  ];
  const stillMissing = allUncertain.filter((item) => !isSystemLimitation(item));
  const investigationLimitations = allUncertain.filter(isSystemLimitation);

  const supported = understanding.planning_assessment.reasons.filter((r) => r.satisfied);
  const outstanding = understanding.planning_assessment.reasons.filter((r) => !r.satisfied);

  return (
    <div className="flex flex-col gap-3">
      {/* 1 — Files to Review: the concrete file paths an engineer should
          open first — the curated evidence package's own must_modify
          tier (ranked, deduped), not the raw component list. The first
          one or two are the closest match; the rest are related context
          — never a fabricated explanation of *why* a file is related
          when the evidence package doesn't actually say. */}
      <div className="rounded-lg border border-line-muted bg-surface-raised px-3 py-2.5">
        <SectionHeading icon={FileCode}>Files to Review</SectionHeading>
        {files.length > 0 ? (
          <div className="mt-1.5 flex flex-col gap-2.5">
            <div>
              <p className="text-[10px] font-semibold tracking-wide text-fg-subtle uppercase">
                Primary
              </p>
              <ul className="mt-0.5 flex flex-col gap-1">
                {primaryFiles.map((path) => (
                  <li key={path}>
                    <p className="truncate font-mono text-[11px] text-fg-secondary">{path}</p>
                    <p className="text-[10.5px] text-fg-subtle">
                      Most central match for this investigation.
                    </p>
                  </li>
                ))}
              </ul>
            </div>
            {relatedFiles.length > 0 && (
              <div>
                <p className="text-[10px] font-semibold tracking-wide text-fg-subtle uppercase">
                  Related
                </p>
                <ul className="mt-0.5 flex flex-col gap-1">
                  {relatedFiles.map((path) => (
                    <li key={path}>
                      <p className="truncate font-mono text-[11px] text-fg-secondary">{path}</p>
                      <p className="text-[10.5px] text-fg-subtle">
                        Related file identified during investigation.
                      </p>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        ) : (
          <div className="mt-1 flex flex-col gap-0.5">
            <p className="text-xs text-fg-subtle">No clearly relevant files identified.</p>
            <p className="text-[11px] text-fg-subtle">
              The investigation did not identify a production file with sufficient evidence to
              recommend — see Technical Details for the full component list.
            </p>
          </div>
        )}
      </div>

      {/* 2 — What we know: the same capability checklist that backs the
          confidence number, spelled out as plain findings instead of left
          as a bare percentage or a capability's internal name. Replaces
          exposing the raw evidence-classification ledger ("Strongly
          supported: 0") right beside the confidence gauge, which read as
          contradicting it — see AdvancedDetailsSection for that ledger's
          new home. */}
      {(supported.length > 0 || outstanding.length > 0) && (
        <div className="rounded-lg border border-line-muted bg-surface-raised px-3 py-2.5">
          <SectionHeading icon={ShieldCheck}>What we know</SectionHeading>
          <ul className="mt-1.5 flex flex-col gap-1">
            {supported.map((r) => (
              <li key={r.description} className="flex items-start gap-1.5 text-xs">
                <Check className="mt-0.5 h-3.5 w-3.5 shrink-0 text-success-fg" aria-hidden="true" />
                <span className="text-fg-secondary">{capabilityCopy(r.description, true)}</span>
              </li>
            ))}
            {outstanding.map((r) => (
              <li key={r.description} className="flex items-start gap-1.5 text-xs">
                <Circle className="mt-0.5 h-3 w-3 shrink-0 text-fg-subtle" aria-hidden="true" />
                <span className="text-fg-subtle">{capabilityCopy(r.description, false)}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* 3 — Still uncertain: genuine content gaps, kept visibly separate
          from a system-side investigation limitation (a failed reasoning
          pass) — the latter must never be presented as "GraphForge looked
          and there's nothing here," it's "GraphForge couldn't finish
          looking." Both used to arrive as one undifferentiated "N things
          still unclear — see Advanced Details" count. */}
      {(stillMissing.length > 0 || investigationLimitations.length > 0) && (
        <div className="rounded-lg border border-line-muted bg-surface-raised px-3 py-2.5">
          <SectionHeading icon={TriangleAlert}>Still uncertain</SectionHeading>
          {stillMissing.length > 0 && (
            <div className="mt-1.5">
              <p className="text-[10px] font-semibold tracking-wide text-fg-subtle uppercase">
                Missing information
              </p>
              <ul className="mt-0.5 flex flex-col gap-0.5">
                {stillMissing.map((item) => (
                  <li key={item} className="text-xs text-fg-secondary">
                    · {item}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {investigationLimitations.length > 0 && (
            <div className="mt-2 rounded-md bg-warning-bg px-2.5 py-1.5">
              <p className="text-[10px] font-semibold tracking-wide text-warning-fg uppercase">
                Analysis could not be completed
              </p>
              <ul className="mt-0.5 flex flex-col gap-0.5">
                {investigationLimitations.map((item) => (
                  <li key={item} className="text-[11px] text-warning-fg">
                    · {translateLimitation(item)}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}

      {/* 4 — Proposed Change: what should change — the actionable
          recommendations from Context Discovery's analysis. An empty list
          here does NOT mean "nothing was found" — it usually means the
          deeper root-cause reasoning step didn't run (see "Analysis could
          not be completed" above), and the copy says so instead of
          reading like GraphForge came up empty. */}
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
        ) : investigationLimitations.length > 0 ? (
          <p className="mt-1 text-xs text-fg-subtle">
            GraphForge has located the relevant code, but hasn&apos;t completed root-cause
            analysis yet — see &ldquo;Analysis could not be completed&rdquo; above.
          </p>
        ) : (
          <p className="mt-1 text-xs text-fg-subtle">No specific change was recommended.</p>
        )}
      </div>

      {/* 5 — Acceptance Criteria: what "done" means — sourced from the
          same field the ticket's own AC section fills when one exists. */}
      {understanding.expected_outcome && (
        <div className="rounded-lg border border-line-muted bg-surface-raised px-3 py-2.5">
          <SectionHeading icon={Check}>Acceptance Criteria</SectionHeading>
          <p className="mt-1 text-xs leading-relaxed text-fg-secondary">
            {understanding.expected_outcome}
          </p>
        </div>
      )}

      {/* 6 — Why GraphForge believes this: the trust footer — confidence
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
              {evidenceCount} piece{evidenceCount === 1 ? "" : "s"} of supporting evidence — see
              the Evidence Trail below.
            </p>
          )}
        </div>
      )}

      {/* 7 — Next step: a real control, not text styled to look like one.
          `next_step` used to render as a plain, non-interactive paragraph
          with a CTA's visual treatment (filled background, chevron) —
          clicking it did nothing. It now does something real: brings the
          actual decision (Approve & Continue / Reject / Refine, further
          down the page) into view, since that's what "next" always means
          here regardless of what this message says. Styled as a link, not
          a second button competing with that decision for primary
          attention. */}
      {understanding.next_step && (
        <button
          type="button"
          onClick={() => {
            const target = document.getElementById("workflow-decision-actions");
            target?.scrollIntoView({ behavior: "smooth", block: "center" });
            target?.focus({ preventScroll: true });
          }}
          className="focus-ring flex items-center gap-1.5 rounded-lg border border-line-muted bg-surface-raised px-3 py-2 text-left text-xs font-medium text-accent-fg hover:bg-surface-hover"
        >
          <ChevronRight className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          {understanding.next_step}
        </button>
      )}
    </div>
  );
}
