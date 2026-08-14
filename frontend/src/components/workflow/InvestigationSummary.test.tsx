import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { InvestigationSummary } from "./InvestigationSummary";
import type { ContextDiscoveryResult, EngineeringUnderstandingDTO } from "../../types/agent";

// ---------------------------------------------------------------------------
// GraphForge Workflow / Context Discovery UX review — the primary
// ("Level 1") screen a non-technical reader and an engineer both land on
// first. These tests pin the specific behaviors the review called out:
// a plain-language "what's supported" checklist instead of the raw
// evidence ledger, a genuine-gap vs system-limitation split instead of one
// undifferentiated "still unclear" bucket, and a working "next step"
// control instead of inert CTA-styled text.
// ---------------------------------------------------------------------------

function makeResult(overrides: Partial<ContextDiscoveryResult> = {}): ContextDiscoveryResult {
  return {
    original_request: "req",
    enriched_text: "req",
    resolved_references: [],
    indexed_repositories: [],
    graph_components: [],
    graph_topics: [],
    repositories: [],
    ranked_repository_names: [],
    implementation_candidates: [],
    explicit_repositories: [],
    suggested_repositories: [],
    selected_repositories: [],
    graph_context_text: "",
    graph_available: true,
    graph_has_data: true,
    planning_metadata: {},
    prompt_version: "4.0",
    goal: "req",
    readiness: "PARTIAL",
    completion_status: "PARTIAL",
    confidence: 0.83,
    capability_confidence: {},
    clarification_rounds: 0,
    blocking_reasons: [],
    remediation_steps: [],
    assumptions: [],
    user_answers: {},
    unresolved_questions: [],
    working_memory: {},
    discovery_report: {
      readiness: "PARTIAL",
      confidence: 0.83,
      headline: "",
      transcript: [],
      confidence_breakdown: [],
      // Deliberately much larger than `investigation.length` below — a
      // real repository's recorded-fact count runs into the thousands
      // (see the raw `findings[].total` the mapper sums) and must never
      // be what "Why GraphForge believes this" reports, or it reads as
      // exactly the internal-metrics jargon ("12,213 recorded facts")
      // this screen exists to not show.
      findings: [{ kind: "repository", total: 12213, items: [] }],
      interpretations: [],
      gaps: [],
      investigation: Array.from({ length: 25 }, (_, i) => ({
        evidence_id: `ev${i}`,
        provider: "graph",
        action: "survey_architecture",
        outcome: "success" as const,
        summary: `Step ${i}`,
        intent: "",
        iteration: 1,
      })),
    },
    ...overrides,
  };
}

function makeUnderstanding(
  overrides: Partial<EngineeringUnderstandingDTO> = {},
): EngineeringUnderstandingDTO {
  return {
    business_goal: "",
    current_situation: "",
    expected_outcome: "",
    repository_summary: { primary: "", supporting: [], ownership: [] },
    architecture_summary: "",
    relevant_areas: [],
    files_to_review: [],
    known_constraints: [],
    missing_information: [],
    unknowns: [],
    evidence_summary: [],
    recommendations: [],
    planning_assessment: { status: "PARTIAL", reasons: [] },
    confidence_explanation: "",
    documentation_status: "",
    next_step: "",
    completion_status: "PARTIAL",
    reasoning_summary: {
      has_reasoning: false,
      degraded: false,
      hypotheses: [],
      contradictions: [],
      open_contradiction_count: 0,
      resolved_contradiction_count: 0,
      strongest_hypothesis_id: null,
      dead_ends: [],
      next_investigation: [],
      last_update: "",
    },
    debug_bundle: null,
    ...overrides,
  };
}

describe("InvestigationSummary", () => {
  it("shows a plain-language 'what we know' checklist, not a raw capability name or evidence ledger", () => {
    render(
      <InvestigationSummary
        result={makeResult()}
        understanding={makeUnderstanding({
          planning_assessment: {
            status: "PARTIAL",
            reasons: [
              { satisfied: true, description: "Work item" },
              { satisfied: true, description: "Repository" },
              { satisfied: false, description: "Documentation" },
            ],
          },
        })}
      />,
    );
    const section = screen.getByText("What we know").closest("div") as HTMLElement;
    // Translated into plain findings, not the raw capability name a
    // non-technical reader has no reason to recognize.
    expect(within(section).getByText("Jira issue identified")).toBeInTheDocument();
    expect(within(section).getByText("Repository identified")).toBeInTheDocument();
    expect(within(section).getByText("No design documentation found")).toBeInTheDocument();
    expect(within(section).queryByText("Work item")).not.toBeInTheDocument();
  });

  it("separates a genuine content gap from a system/investigation limitation", () => {
    render(
      <InvestigationSummary
        result={makeResult()}
        understanding={makeUnderstanding({
          missing_information: [
            "No design documentation was found for this work.",
            "Engineering synthesis (LLM reasoning pass) failed — see logs for the underlying error.",
          ],
          unknowns: [
            { category: "unknown", description: "No design documentation was found for this work." },
            {
              category: "unknown",
              description:
                "Engineering synthesis (LLM reasoning pass) failed — see logs for the underlying error.",
            },
          ],
        })}
      />,
    );

    const section = screen.getByText("Still uncertain").closest("div") as HTMLElement;
    const stillMissing = within(section)
      .getByText("Missing information")
      .closest("div") as HTMLElement;
    expect(
      within(stillMissing).getByText(/No design documentation was found/),
    ).toBeInTheDocument();
    expect(
      within(stillMissing).queryByText(/Engineering (synthesis|analysis)/),
    ).not.toBeInTheDocument();

    const limitation = within(section)
      .getByText("Analysis could not be completed")
      .closest("div") as HTMLElement;
    // Translated into plain language — not the raw "Engineering synthesis
    // (LLM reasoning pass)" backend wording.
    expect(
      within(limitation).getByText(/Engineering analysis could not be completed for this run/),
    ).toBeInTheDocument();
    expect(within(limitation).queryByText(/Engineering synthesis/)).not.toBeInTheDocument();
    expect(
      within(limitation).queryByText(/No design documentation was found/),
    ).not.toBeInTheDocument();
  });

  it("never presents a degraded reasoning pass as 'nothing is missing'", () => {
    // A run where the *only* uncertainty is a system limitation must still
    // show it, distinctly labeled — not silently drop the whole section
    // just because there's no genuine content gap alongside it.
    render(
      <InvestigationSummary
        result={makeResult()}
        understanding={makeUnderstanding({
          missing_information: [
            "Engineering synthesis (hypothesis reasoning, cross-source insight) did not run for this investigation.",
          ],
          unknowns: [
            {
              category: "unknown",
              description:
                "Engineering synthesis (hypothesis reasoning, cross-source insight) did not run for this investigation.",
            },
          ],
        })}
      />,
    );
    expect(screen.getByText("Still uncertain")).toBeInTheDocument();
    expect(screen.getByText("Analysis could not be completed")).toBeInTheDocument();
    expect(screen.queryByText("Missing information")).not.toBeInTheDocument();
  });

  it("splits Files to Review into Primary and Related, never inventing why a related file matters", () => {
    render(
      <InvestigationSummary
        result={makeResult()}
        understanding={makeUnderstanding({
          files_to_review: ["meter.py", "ingest_raw_data.py", "internal_manifest.py"],
        })}
      />,
    );
    expect(screen.getByText("meter.py")).toBeInTheDocument();
    expect(screen.getByText("ingest_raw_data.py")).toBeInTheDocument();
    expect(screen.getByText("Related file identified during investigation.")).toBeInTheDocument();
  });

  it("renders the honest empty state when curation found no files, unchanged", () => {
    render(<InvestigationSummary result={makeResult()} understanding={makeUnderstanding()} />);
    expect(screen.getByText("No clearly relevant files identified.")).toBeInTheDocument();
  });

  it("reports the real evidence-item count, not the much larger raw recorded-fact total", () => {
    render(
      <InvestigationSummary
        result={makeResult()}
        understanding={makeUnderstanding({ confidence_explanation: "Completed: Repository." })}
      />,
    );
    expect(screen.getByText(/25 pieces of supporting evidence/)).toBeInTheDocument();
    expect(screen.queryByText(/recorded facts?/)).not.toBeInTheDocument();
    expect(screen.queryByText(/12213|12,213/)).not.toBeInTheDocument();
  });

  it("'next step' is a real control that brings the decision into view, not inert styled text", async () => {
    const user = userEvent.setup();
    const target = document.createElement("div");
    target.id = "workflow-decision-actions";
    target.scrollIntoView = vi.fn();
    const focusSpy = vi.spyOn(target, "focus");
    document.body.appendChild(target);

    render(
      <InvestigationSummary
        result={makeResult()}
        understanding={makeUnderstanding({ next_step: "Resolve the open questions above." })}
      />,
    );

    const control = screen.getByRole("button", { name: /Resolve the open questions above\./ });
    await user.click(control);

    expect(target.scrollIntoView).toHaveBeenCalled();
    expect(focusSpy).toHaveBeenCalled();

    document.body.removeChild(target);
  });

  it("renders nothing for next step when there is none — no dead control left behind", () => {
    render(<InvestigationSummary result={makeResult()} understanding={makeUnderstanding()} />);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});
