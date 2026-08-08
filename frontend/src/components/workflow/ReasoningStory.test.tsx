import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ReasoningOverview } from "./ReasoningOverview";
import { InvestigationTimeline } from "./InvestigationTimeline";
import { KnowledgeLedger } from "./KnowledgeLedger";
import { UnknownsAndNext } from "./UnknownsAndNext";
import type {
  ContextDiscoveryResult,
  EngineeringUnderstandingDTO,
  HypothesisDTO,
  InvestigationStep,
} from "../../types/agent";

// ---------------------------------------------------------------------------
// Regression tests for the "investigation story" components introduced by
// the Reasoning Visualization redesign — the 8 questions in the design
// review's own 10-second test, exercised directly against each node rather
// than only through the full `ContextExplorerPanel` integration tests in
// ContextDiscoveryUi.test.tsx.
// ---------------------------------------------------------------------------

function makeResult(overrides: Partial<ContextDiscoveryResult> = {}): ContextDiscoveryResult {
  return {
    original_request: "Why do rate associations duplicate after ingestion?",
    enriched_text: "Why do rate associations duplicate after ingestion?",
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
    goal: "discover_context",
    readiness: "READY",
    completion_status: "COMPLETED",
    confidence: 0.86,
    capability_confidence: {},
    clarification_rounds: 0,
    blocking_reasons: [],
    remediation_steps: [],
    assumptions: [],
    user_answers: {},
    unresolved_questions: [],
    working_memory: {},
    discovery_report: {
      readiness: "READY",
      confidence: 0.86,
      headline: "",
      transcript: [],
      confidence_breakdown: [],
      findings: [],
      interpretations: [],
      gaps: [],
      investigation: [],
    },
    ...overrides,
  };
}

function makeUnderstanding(
  overrides: Partial<EngineeringUnderstandingDTO> = {},
): EngineeringUnderstandingDTO {
  return {
    business_goal: "Determine why rate associations duplicate after ingestion.",
    current_situation: "The export transform doesn't close out end_datetime on re-ingestion.",
    expected_outcome: "Each active rate association appears exactly once.",
    repository_summary: { primary: "ds-databricks-soco", supporting: [], ownership: [] },
    architecture_summary: "",
    relevant_areas: [],
    files_to_review: [],
    known_constraints: [],
    missing_information: [],
    unknowns: [],
    evidence_summary: [],
    recommendations: [],
    planning_assessment: { status: "READY", reasons: [] },
    confidence_explanation: "Completed: Repository, Architecture.",
    documentation_status: "",
    next_step: "",
    completion_status: "COMPLETED",
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

function hypothesis(overrides: Partial<HypothesisDTO>): HypothesisDTO {
  return {
    id: "hyp_0",
    description: "The export transform never closes out end_datetime.",
    status: "supported",
    confidence: 0.78,
    supporting_evidence: [],
    contradicting_evidence: [],
    is_strongest: false,
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// ReasoningOverview — question / confidence / degraded ribbon
// ---------------------------------------------------------------------------

describe("ReasoningOverview", () => {
  it("answers 'what was it trying to determine' and 'what does it believe' from real fields", () => {
    render(<ReasoningOverview result={makeResult()} understanding={makeUnderstanding()} />);
    expect(
      screen.getByText("Determine why rate associations duplicate after ingestion."),
    ).toBeInTheDocument();
    expect(
      screen.getByText("The export transform doesn't close out end_datetime on re-ingestion."),
    ).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "86% confidence" })).toBeInTheDocument();
    expect(screen.getByText("READY")).toBeInTheDocument();
  });

  it("degrades gracefully to `result` alone when `understanding` hasn't loaded yet", () => {
    render(<ReasoningOverview result={makeResult()} understanding={null} />);
    // Falls back to the raw request text — never blank, never crashes.
    expect(
      screen.getByText("Why do rate associations duplicate after ingestion?"),
    ).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "86% confidence" })).toBeInTheDocument();
    // No "Currently believes" line without understanding.current_situation.
    expect(screen.queryByText(/Currently believes/)).not.toBeInTheDocument();
  });

  it("never renders NaN — an invalid confidence reads as 'not yet available'", () => {
    render(
      <ReasoningOverview
        result={makeResult({ confidence: undefined as unknown as number })}
        understanding={null}
      />,
    );
    expect(screen.getByRole("img", { name: "Confidence not yet available" })).toBeInTheDocument();
    expect(screen.queryByText(/NaN/)).not.toBeInTheDocument();
  });

  it("shows the degraded ribbon, distinctly from a clean run, only when reasoning_summary.degraded is true", () => {
    const { rerender } = render(
      <ReasoningOverview result={makeResult()} understanding={makeUnderstanding()} />,
    );
    expect(screen.queryByText(/did not complete this pass/)).not.toBeInTheDocument();

    rerender(
      <ReasoningOverview
        result={makeResult()}
        understanding={makeUnderstanding({
          reasoning_summary: {
            ...makeUnderstanding().reasoning_summary,
            degraded: true,
          },
        })}
      />,
    );
    expect(screen.getByText("Reasoning synthesis did not complete this pass.")).toBeInTheDocument();
    expect(
      screen.getByText(/different from an investigation that genuinely found nothing to weigh/),
    ).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// InvestigationTimeline — grouped by iteration, 4 distinct outcomes, bounded
// ---------------------------------------------------------------------------

describe("InvestigationTimeline", () => {
  const steps: InvestigationStep[] = [
    {
      evidence_id: "ev1",
      provider: "graph",
      action: "traverse_architecture_graph",
      outcome: "success",
      summary: "Found the export transform.",
      intent: "",
      iteration: 1,
    },
    {
      evidence_id: "ev2",
      provider: "graph",
      action: "check_uniqueness_constraint",
      outcome: "not_found",
      summary: "No constraint recorded.",
      intent: "",
      iteration: 2,
    },
    {
      evidence_id: "ev3",
      provider: "confluence",
      action: "search_design_docs",
      outcome: "unavailable",
      summary: "Confluence is not connected.",
      intent: "",
      iteration: 2,
    },
    {
      evidence_id: "ev4",
      provider: "testrail",
      action: "search_test_coverage",
      outcome: "failed",
      summary: "Request timed out.",
      intent: "",
      iteration: 3,
    },
  ];

  it("groups steps by iteration and shows a distinct label for each of the 4 real outcomes", () => {
    render(<InvestigationTimeline steps={steps} nextInvestigation={[]} />);
    expect(screen.getByText("Evidence gained")).toBeInTheDocument();
    expect(screen.getByText("No evidence found")).toBeInTheDocument();
    expect(screen.getByText("Unavailable")).toBeInTheDocument();
    expect(screen.getByText("Failed")).toBeInTheDocument();
    // Iteration 2 groups two steps under one badge.
    expect(screen.getByText("No constraint recorded.")).toBeInTheDocument();
    expect(screen.getByText("Confluence is not connected.")).toBeInTheDocument();
  });

  it("shows what's next, closing the narrative loop, when next_investigation has an entry", () => {
    render(
      <InvestigationTimeline
        steps={steps}
        nextInvestigation={[{ capability: "architecture", label: "Ingestion write semantics", priority: 0.71 }]}
      />,
    );
    expect(screen.getByText("Investigating next: Ingestion write semantics")).toBeInTheDocument();
  });

  it("renders nothing when there is genuinely no investigation data (never a broken empty shell)", () => {
    const { container } = render(<InvestigationTimeline steps={[]} nextInvestigation={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("stays bounded regardless of repository size — one row per real step, never per graph node", () => {
    // A 1,000+ node repository has zero effect on this list: the engine
    // still only runs a handful of real reasoning cycles. Simulate the
    // upper end of that (14 steps, matching MAX_CYCLES) and confirm the
    // DOM stays a short, real list, not a synthesized wall.
    const manySteps: InvestigationStep[] = Array.from({ length: 14 }, (_, i) => ({
      evidence_id: `ev${i}`,
      provider: "graph",
      action: `action_${i}`,
      outcome: "success" as const,
      summary: `Step ${i}`,
      intent: "",
      iteration: Math.floor(i / 2) + 1,
    }));
    render(<InvestigationTimeline steps={manySteps} nextInvestigation={[]} />);
    // 14 real steps rendered — no truncation needed at this scale, and no
    // dependency at all on how large the underlying repository graph was.
    expect(screen.getAllByText(/^Step \d+$/)).toHaveLength(14);
  });
});

// ---------------------------------------------------------------------------
// KnowledgeLedger — the 4-bucket "what do we know" ledger
// ---------------------------------------------------------------------------

describe("KnowledgeLedger", () => {
  it("buckets hypotheses by status into Strongly supported / Inferred / Contradicted, and unknowns separately", () => {
    render(
      <KnowledgeLedger
        understanding={makeUnderstanding({
          reasoning_summary: {
            has_reasoning: true,
            degraded: false,
            hypotheses: [
              hypothesis({ id: "hyp_0", status: "supported", description: "Supported explanation." }),
              hypothesis({ id: "hyp_1", status: "unknown", description: "Inferred explanation." }),
              hypothesis({ id: "hyp_2", status: "rejected", description: "Rejected explanation." }),
            ],
            contradictions: [],
            open_contradiction_count: 0,
            resolved_contradiction_count: 0,
            strongest_hypothesis_id: "hyp_0",
            dead_ends: [],
            next_investigation: [],
            last_update: "",
          },
          missing_information: ["Whether the write is append or merge"],
          unknowns: [{ category: "unknown", description: "Whether the write is append or merge" }],
        })}
      />,
    );
    expect(screen.getByText("Strongly supported")).toBeInTheDocument();
    expect(screen.getByText(/Supported explanation\./)).toBeInTheDocument();
    expect(screen.getByText("Inferred")).toBeInTheDocument();
    expect(screen.getByText(/Inferred explanation\./)).toBeInTheDocument();
    expect(screen.getByText("Contradicted")).toBeInTheDocument();
    expect(screen.getByText(/Rejected explanation\./)).toBeInTheDocument();
    expect(screen.getByText("Unknown")).toBeInTheDocument();
    expect(screen.getByText(/Whether the write is append or merge/)).toBeInTheDocument();
    // Not "Confirmed" — the underlying text is evidence-grounded synthesis,
    // not deterministic proof, and the data contract makes no stronger claim.
    expect(screen.queryByText("Confirmed")).not.toBeInTheDocument();
  });

  it("falls back to the grounded root-cause text for 'Strongly supported' when zero hypotheses ran", () => {
    render(
      <KnowledgeLedger
        understanding={makeUnderstanding({
          current_situation: "The export transform never closes out end_datetime.",
        })}
      />,
    );
    expect(screen.getByText(/The export transform never closes out end_datetime\./)).toBeInTheDocument();
    // No hypotheses ran at all (not degraded — just nothing to weigh), so
    // Inferred/Contradicted show the honest "none found," never the
    // degraded-unavailable placeholder.
    expect(screen.getByText("Inferred")).toBeInTheDocument();
    const inferredColumn = screen.getByText("Inferred").closest("div")?.parentElement;
    expect(within(inferredColumn as HTMLElement).getByText("None found this pass.")).toBeInTheDocument();
  });

  it("treats an open contradiction as Contradicted evidence, distinct from a resolved one", () => {
    render(
      <KnowledgeLedger
        understanding={makeUnderstanding({
          reasoning_summary: {
            has_reasoning: true,
            degraded: false,
            hypotheses: [],
            contradictions: [
              { id: "c1", description: "Open conflict.", evidence_for: [], evidence_against: [], resolved: false, resolution_note: "" },
              { id: "c2", description: "Resolved conflict.", evidence_for: [], evidence_against: [], resolved: true, resolution_note: "note" },
            ],
            open_contradiction_count: 1,
            resolved_contradiction_count: 1,
            strongest_hypothesis_id: null,
            dead_ends: [],
            next_investigation: [],
            last_update: "",
          },
        })}
      />,
    );
    expect(screen.getByText(/Open conflict\./)).toBeInTheDocument();
    expect(screen.queryByText(/Resolved conflict\./)).not.toBeInTheDocument();
  });

  it("shows an explicit 'unavailable' placeholder for Inferred/Contradicted under degraded synthesis — never a clean-looking zero", () => {
    render(
      <KnowledgeLedger
        understanding={makeUnderstanding({
          current_situation: "Deterministic root cause text still present.",
          missing_information: ["Engineering synthesis (LLM reasoning pass) failed — see logs."],
          reasoning_summary: {
            has_reasoning: false,
            degraded: true,
            hypotheses: [],
            contradictions: [],
            open_contradiction_count: 0,
            resolved_contradiction_count: 0,
            strongest_hypothesis_id: null,
            dead_ends: [],
            next_investigation: [],
            last_update: "Cycle 5: synthesis degraded to a deterministic summary over 7 evidence record(s).",
          },
        })}
      />,
    );
    const unavailable = screen.getAllByText("Not available — reasoning synthesis didn't complete this pass.");
    expect(unavailable).toHaveLength(2); // Inferred + Contradicted
    // Strongly supported and Unknown are not LLM-hypothesis-dependent —
    // they still populate from the deterministic summary.
    expect(screen.getByText(/Deterministic root cause text still present\./)).toBeInTheDocument();
    expect(
      screen.getByText(/Engineering synthesis \(LLM reasoning pass\) failed — see logs\./),
    ).toBeInTheDocument();
  });

  it("renders nothing when every bucket would genuinely be empty", () => {
    const { container } = render(
      <KnowledgeLedger understanding={makeUnderstanding({ current_situation: "" })} />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});

// ---------------------------------------------------------------------------
// UnknownsAndNext — always-visible unknowns + closing "what's next"
// ---------------------------------------------------------------------------

describe("UnknownsAndNext", () => {
  it("shows unknowns as chips, deduped across missing_information and the unknowns list", () => {
    render(
      <UnknownsAndNext
        missingInformation={["Whether writes are concurrent"]}
        unknowns={[
          { category: "unknown", description: "Whether writes are concurrent" },
          { category: "unknown", description: "Whether a uniqueness constraint exists" },
          { category: "known", description: "Something already resolved — must not appear" },
        ]}
        nextInvestigation={[]}
      />,
    );
    expect(screen.getAllByText("Whether writes are concurrent")).toHaveLength(1);
    expect(screen.getByText("Whether a uniqueness constraint exists")).toBeInTheDocument();
    expect(screen.queryByText("Something already resolved — must not appear")).not.toBeInTheDocument();
  });

  it("shows what's next with its priority, and hides the CTA when there is none", () => {
    const { rerender } = render(
      <UnknownsAndNext missingInformation={[]} unknowns={[]} nextInvestigation={[]} />,
    );
    expect(screen.queryByText("Investigating next")).not.toBeInTheDocument();

    rerender(
      <UnknownsAndNext
        missingInformation={[]}
        unknowns={[]}
        nextInvestigation={[{ capability: "architecture", label: "Ingestion semantics", priority: 0.71 }]}
      />,
    );
    expect(screen.getByText("Investigating next")).toBeInTheDocument();
    expect(screen.getByText("Ingestion semantics")).toBeInTheDocument();
  });

  it("renders nothing when there are no unknowns and nothing planned next", () => {
    const { container } = render(
      <UnknownsAndNext missingInformation={[]} unknowns={[]} nextInvestigation={[]} />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});
